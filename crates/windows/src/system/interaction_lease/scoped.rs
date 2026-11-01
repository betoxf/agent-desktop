use std::path::Path;
use std::time::Duration;

use agent_desktop_core::{
    AdapterError, Deadline, InteractionLease, InteractionLeaseScope, ProcessLeaseGuard,
};
use windows_sys::Win32::Foundation::{
    CloseHandle, ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, GENERIC_READ, GENERIC_WRITE,
    INVALID_HANDLE_VALUE,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_ALWAYS,
};

const SHARED_MODE: u32 = FILE_SHARE_READ | FILE_SHARE_WRITE;

pub(super) fn acquire_scope(
    scope: &InteractionLeaseScope,
    deadline: Deadline,
) -> Result<InteractionLease, AdapterError> {
    let stems = scope.app_lock_stems();
    if stems.is_empty() {
        return super::acquire(deadline);
    }
    let root = super::directory::canonical_root()?;
    let process_guard = ProcessLeaseGuard::acquire_apps(&stems, deadline)?;
    let process_contention = process_guard.contention_count();
    let global_path = super::lock_path_under(&root)?;
    let directory = global_path
        .parent()
        .ok_or_else(|| AdapterError::internal("interaction lease path has no parent directory"))?;
    super::directory::ensure_private(directory)?;
    let apps_dir = directory.join("apps");
    super::directory::ensure_private(&apps_dir)?;
    let (global_handle, global_contention) = open_lock(&global_path, deadline, SHARED_MODE)?;
    let mut handles = vec![global_handle];
    let mut contention = global_contention.saturating_add(process_contention);
    for stem in &stems {
        let path = apps_dir.join(format!("{stem}.lock"));
        let (handle, file_contention) = open_lock(&path, deadline, 0)?;
        handles.push(handle);
        contention = contention.saturating_add(file_contention);
    }
    Ok(InteractionLease::guarded_with_contention(
        deadline,
        ScopedLeaseGuard {
            _handles: handles,
            _process: process_guard,
        },
        contention,
    ))
}

fn open_lock(
    path: &Path,
    deadline: Deadline,
    share_mode: u32,
) -> Result<(super::OwnedLockHandle, u64), AdapterError> {
    let wide = super::identity::to_wide(path);
    let mut contention = 0_u64;
    loop {
        if deadline.is_expired() {
            return Err(super::lock_timeout(deadline, path, contention));
        }
        let handle = unsafe {
            CreateFileW(
                wide.as_ptr(),
                GENERIC_READ | GENERIC_WRITE,
                share_mode,
                std::ptr::null(),
                OPEN_ALWAYS,
                FILE_ATTRIBUTE_NORMAL,
                std::ptr::null_mut(),
            )
        };
        if handle != INVALID_HANDLE_VALUE {
            if deadline.is_expired() {
                unsafe { CloseHandle(handle) };
                return Err(super::lock_timeout(deadline, path, contention));
            }
            return Ok((super::OwnedLockHandle(handle), contention));
        }
        let error = std::io::Error::last_os_error();
        match error.raw_os_error().map(|code| code as u32) {
            Some(ERROR_SHARING_VIOLATION) => {
                contention = contention.saturating_add(1);
                let remaining = deadline.remaining();
                if remaining.is_zero() {
                    return Err(super::lock_timeout(deadline, path, contention));
                }
                std::thread::sleep(remaining.min(Duration::from_millis(10)));
            }
            Some(ERROR_ACCESS_DENIED) => {
                let rid = super::sid::process_token_integrity_rid().ok();
                return Err(super::integrity_denied(rid));
            }
            _ => {
                return Err(AdapterError::internal(format!(
                    "CreateFileW failed acquiring the interaction lease: {error}"
                )));
            }
        }
    }
}

struct ScopedLeaseGuard {
    #[allow(dead_code)]
    _handles: Vec<super::OwnedLockHandle>,
    #[allow(dead_code)]
    _process: ProcessLeaseGuard,
}
