use std::collections::HashSet;
use std::sync::{Condvar, LazyLock, Mutex, MutexGuard};
use std::time::Duration;

use crate::{AdapterError, Deadline};

struct Table {
    exclusive: bool,
    exclusive_waiters: u32,
    apps: HashSet<String>,
}

static TABLE: LazyLock<Mutex<Table>> = LazyLock::new(|| {
    Mutex::new(Table {
        exclusive: false,
        exclusive_waiters: 0,
        apps: HashSet::new(),
    })
});
static TABLE_CV: Condvar = Condvar::new();

pub struct ProcessLeaseGuard {
    kind: GuardKind,
    contention_count: u64,
}

enum GuardKind {
    Exclusive,
    Apps(Vec<String>),
}

impl ProcessLeaseGuard {
    pub fn acquire(deadline: Deadline) -> Result<Self, AdapterError> {
        let mut table = lock_table();
        let mut contention_count = 0_u64;
        table.exclusive_waiters = table.exclusive_waiters.saturating_add(1);
        loop {
            if !table.exclusive && table.apps.is_empty() {
                table.exclusive = true;
                table.exclusive_waiters = table.exclusive_waiters.saturating_sub(1);
                return Ok(Self {
                    kind: GuardKind::Exclusive,
                    contention_count,
                });
            }
            match wait_for_release(table, deadline, &mut contention_count) {
                Ok(next) => table = next,
                Err(error) => {
                    abandon_exclusive_wait();
                    return Err(error);
                }
            }
        }
    }

    /// Holds every stem or none of them. Callers pass stems in lock order.
    pub fn acquire_apps(stems: &[String], deadline: Deadline) -> Result<Self, AdapterError> {
        if stems.is_empty() {
            return Self::acquire(deadline);
        }
        let mut table = lock_table();
        let mut contention_count = 0_u64;
        loop {
            let blocked = table.exclusive
                || table.exclusive_waiters > 0
                || stems.iter().any(|stem| table.apps.contains(stem));
            if !blocked {
                for stem in stems {
                    table.apps.insert(stem.clone());
                }
                return Ok(Self {
                    kind: GuardKind::Apps(stems.to_vec()),
                    contention_count,
                });
            }
            table = wait_for_release(table, deadline, &mut contention_count)?;
        }
    }

    pub fn contention_count(&self) -> u64 {
        self.contention_count
    }
}

impl Drop for ProcessLeaseGuard {
    fn drop(&mut self) {
        let mut table = lock_table();
        match &self.kind {
            GuardKind::Exclusive => table.exclusive = false,
            GuardKind::Apps(stems) => {
                for stem in stems {
                    table.apps.remove(stem);
                }
            }
        }
        drop(table);
        TABLE_CV.notify_all();
    }
}

fn abandon_exclusive_wait() {
    let mut table = lock_table();
    table.exclusive_waiters = table.exclusive_waiters.saturating_sub(1);
    drop(table);
    TABLE_CV.notify_all();
}

fn lock_table() -> MutexGuard<'static, Table> {
    TABLE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn wait_for_release(
    table: MutexGuard<'static, Table>,
    deadline: Deadline,
    contention_count: &mut u64,
) -> Result<MutexGuard<'static, Table>, AdapterError> {
    if deadline.remaining().is_zero() {
        return Err(timeout(deadline, *contention_count));
    }
    *contention_count = contention_count.saturating_add(1);
    let slice = deadline.remaining().min(Duration::from_millis(10));
    let (table, _) = TABLE_CV
        .wait_timeout(table, slice)
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if deadline.remaining().is_zero() {
        return Err(timeout(deadline, *contention_count));
    }
    Ok(table)
}

fn timeout(deadline: Deadline, contention_count: u64) -> AdapterError {
    deadline.timeout_error().with_details(serde_json::json!({
        "kind": "interaction_process_lock_timeout",
        "contention_count": contention_count,
    }))
}
