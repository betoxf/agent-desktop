#![cfg(unix)]

use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use super::{Deadline, ErrorCode, acquire_scoped_at};
use crate::ProcessId;
use crate::session::lease_scope::InteractionLeaseScope;

fn test_root(label: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!(
        "agent-desktop-scoped-{label}-{}",
        crate::refs::new_snapshot_id()
    ));
    std::fs::create_dir_all(&root).unwrap();
    root
}

fn notes(pid: u32) -> InteractionLeaseScope {
    InteractionLeaseScope::app(Some("com.example.notes"), Some(ProcessId::new(pid)))
}

fn mail(pid: u32) -> InteractionLeaseScope {
    InteractionLeaseScope::app(Some("com.example.mail"), Some(ProcessId::new(pid)))
}

#[test]
fn two_apps_run_concurrently() {
    let _guard = super::exclusive_test_lock();
    let root = test_root("concurrent");
    let pair = Arc::new((Mutex::new(0_u8), Condvar::new()));
    let overlapped = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let notes = notes(11);
    let mail = mail(22);
    std::thread::scope(|scope| {
        for app in [notes, mail] {
            let pair = Arc::clone(&pair);
            let overlapped = Arc::clone(&overlapped);
            let root = root.clone();
            scope.spawn(move || {
                let _lease =
                    acquire_scoped_at(&app, Deadline::after(2_000).unwrap(), &root).unwrap();
                let (lock, cv) = &*pair;
                let mut count = lock.lock().unwrap();
                *count += 1;
                cv.notify_all();
                let (count, wait) = cv
                    .wait_timeout_while(count, Duration::from_secs(5), |count| *count < 2)
                    .unwrap();
                if *count == 2 && !wait.timed_out() {
                    overlapped.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                }
            });
        }
    });
    assert_eq!(
        overlapped.load(std::sync::atomic::Ordering::SeqCst),
        2,
        "leases for different apps did not overlap"
    );
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn the_same_app_is_serialized() {
    let _guard = super::exclusive_test_lock();
    let root = test_root("same-app");
    let held = notes(11);
    let _first = acquire_scoped_at(&held, Deadline::after(1_000).unwrap(), &root).unwrap();
    let error = match acquire_scoped_at(&notes(99), Deadline::after(40).unwrap(), &root) {
        Ok(_) => panic!("a second lease for the same bundle must wait"),
        Err(error) => error,
    };
    assert_eq!(error.code, ErrorCode::Timeout);
    let pid_only = InteractionLeaseScope::app(None, Some(ProcessId::new(11)));
    let error = match acquire_scoped_at(&pid_only, Deadline::after(40).unwrap(), &root) {
        Ok(_) => panic!("a pid lock must wait behind the same pid"),
        Err(error) => error,
    };
    assert_eq!(error.code, ErrorCode::Timeout);
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn the_machine_lock_excludes_app_locks() {
    let _guard = super::exclusive_test_lock();
    let root = test_root("global-excludes");
    let app = notes(11);
    let global = InteractionLeaseScope::global();
    let held = acquire_scoped_at(&global, Deadline::after(1_000).unwrap(), &root).unwrap();
    let error = match acquire_scoped_at(&app, Deadline::after(40).unwrap(), &root) {
        Ok(_) => panic!("an app lease must wait behind the machine lock"),
        Err(error) => error,
    };
    assert_eq!(error.code, ErrorCode::Timeout);
    drop(held);
    let held = acquire_scoped_at(&app, Deadline::after(1_000).unwrap(), &root).unwrap();
    let error = match acquire_scoped_at(&global, Deadline::after(40).unwrap(), &root) {
        Ok(_) => panic!("the machine lock must wait behind an app lease"),
        Err(error) => error,
    };
    assert_eq!(error.code, ErrorCode::Timeout);
    assert!(
        error.details.as_ref().unwrap()["contention_count"]
            .as_u64()
            .unwrap()
            >= 1
    );
    drop(held);
    acquire_scoped_at(&global, Deadline::after(1_000).unwrap(), &root).unwrap();
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn a_crashed_app_holder_releases_its_lock() {
    let _guard = super::exclusive_test_lock();
    let root = test_root("crash");
    let ready = root.join("ready");
    let held = notes(4242);
    let other = mail(7);
    let mut child = std::process::Command::new(std::env::current_exe().unwrap())
        .arg("--exact")
        .arg("session::interaction_lease::scoped_tests::subprocess_app_holder")
        .arg("--nocapture")
        .env("AGENT_DESKTOP_APP_LOCK_READY", &ready)
        .env("AGENT_DESKTOP_APP_LOCK_ROOT", &root)
        .env("AGENT_DESKTOP_APP_LOCK_BUNDLE", "com.example.notes")
        .env("AGENT_DESKTOP_APP_LOCK_PID", "4242")
        .stdin(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    assert!(
        wait_for_marker(&ready),
        "subprocess did not acquire the application lease"
    );
    acquire_scoped_at(&other, Deadline::after(1_000).unwrap(), &root)
        .expect("a different application must run while the holder is alive");
    let error = match acquire_scoped_at(&held, Deadline::after(40).unwrap(), &root) {
        Ok(_) => panic!("the held application must stay locked"),
        Err(error) => error,
    };
    assert_eq!(error.code, ErrorCode::Timeout);
    assert_eq!(error.details.unwrap()["kind"], "lock_timeout");
    child.kill().unwrap();
    reap_child(&mut child, Duration::from_secs(60));
    acquire_scoped_at(&held, Deadline::after(1_000).unwrap(), &root)
        .expect("a killed holder must release the application lease");
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn subprocess_app_holder() {
    let Some(ready_path) = std::env::var_os("AGENT_DESKTOP_APP_LOCK_READY") else {
        return;
    };
    let root = std::path::PathBuf::from(std::env::var_os("AGENT_DESKTOP_APP_LOCK_ROOT").unwrap());
    let bundle = std::env::var("AGENT_DESKTOP_APP_LOCK_BUNDLE").unwrap();
    let pid = std::env::var("AGENT_DESKTOP_APP_LOCK_PID")
        .unwrap()
        .parse::<u32>()
        .unwrap();
    let scope = InteractionLeaseScope::app(Some(&bundle), Some(ProcessId::new(pid)));
    let _lease = acquire_scoped_at(&scope, Deadline::after(5_000).unwrap(), &root).unwrap();
    std::fs::write(ready_path, b"ready").unwrap();
    let mut released = Vec::new();
    let _ = std::io::Read::read_to_end(&mut std::io::stdin(), &mut released);
}

fn wait_for_marker(path: &std::path::Path) -> bool {
    let deadline = std::time::Instant::now() + Duration::from_secs(60);
    while !path.is_file() && std::time::Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
    path.is_file()
}

#[test]
fn an_app_lease_duplicate_keeps_that_app_locked_and_leaves_another_free() {
    let _guard = super::exclusive_test_lock();
    let root = test_root("retain");
    let notes = notes(11);
    let lease = acquire_scoped_at(&notes, Deadline::after(2_000).unwrap(), &root).unwrap();
    let refused = lease.duplicate_inheritable_fd().unwrap_err();
    assert_eq!(refused.code, ErrorCode::PlatformNotSupported);
    let held = lease
        .duplicate_lock_fds()
        .expect("the worker can retain the locks");
    assert_eq!(held.len(), 3);
    drop(lease);
    acquire_scoped_at(&mail(22), Deadline::after(1_000).unwrap(), &root)
        .expect("a different application still runs");
    let blocked = match acquire_scoped_at(&notes, Deadline::after(200).unwrap(), &root) {
        Ok(_) => panic!("the retained application lease must stay held"),
        Err(error) => error,
    };
    assert_eq!(blocked.code, ErrorCode::Timeout);
    drop(held);
    acquire_scoped_at(&notes, Deadline::after(1_000).unwrap(), &root)
        .expect("dropping the copies releases the application");
    std::fs::remove_dir_all(root).unwrap();
}

fn reap_child(child: &mut std::process::Child, timeout: Duration) {
    let deadline = std::time::Instant::now() + timeout;
    loop {
        if child.try_wait().unwrap().is_some() {
            return;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "lock-holder subprocess did not exit after kill"
        );
        std::thread::sleep(Duration::from_millis(10));
    }
}
