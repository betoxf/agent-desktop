#[cfg(unix)]
use std::path::PathBuf;
use std::sync::Arc;

use crate::{AdapterError, Deadline};
#[cfg(unix)]
use crate::{ErrorCode, storage::file_lock::FileLock};

pub struct InteractionLease {
    _guard: Option<Arc<dyn Send + Sync>>,
    #[cfg(unix)]
    files: Option<UnixHeld>,
    #[cfg(unix)]
    _process_guard: Option<Arc<crate::session::process_lease_guard::ProcessLeaseGuard>>,
    deadline: Deadline,
    contention_count: u64,
}

#[cfg(unix)]
#[derive(Clone)]
struct UnixHeld {
    #[allow(dead_code)]
    app_locks: Vec<Arc<FileLock>>,
    primary: Arc<FileLock>,
    inheritable: bool,
}

#[cfg(unix)]
pub const INTERACTION_LEASE_FD_ENV: &str = "AGENT_DESKTOP_INTERACTION_LEASE_FD";

impl InteractionLease {
    pub fn guarded(
        deadline: Deadline,
        guard: impl Send + Sync + 'static,
    ) -> Result<Self, AdapterError> {
        Ok(Self {
            _guard: Some(Arc::new(guard)),
            #[cfg(unix)]
            files: None,
            #[cfg(unix)]
            _process_guard: None,
            deadline,
            contention_count: 0,
        })
    }

    pub fn deadline(&self) -> Deadline {
        self.deadline
    }

    /// Shares the held interaction ownership with a fresh recovery deadline.
    pub fn with_deadline(&self, deadline: Deadline) -> Self {
        Self {
            _guard: self._guard.clone(),
            #[cfg(unix)]
            files: self.files.clone(),
            #[cfg(unix)]
            _process_guard: self._process_guard.clone(),
            deadline,
            contention_count: self.contention_count,
        }
    }

    pub fn contention_count(&self) -> u64 {
        self.contention_count
    }

    #[cfg(unix)]
    pub fn duplicate_inheritable_fd(&self) -> Result<std::os::fd::OwnedFd, AdapterError> {
        let files = self
            .files
            .as_ref()
            .filter(|files| files.inheritable)
            .ok_or_else(|| {
                AdapterError::not_supported("interaction lease descriptor inheritance")
            })?;
        files.primary.duplicate_inheritable()
    }

    /// Copies of every lock this lease holds, including an application lease.
    /// The copies keep those locks after this lease is dropped. They are not
    /// the descriptor a child adopts as the machine-wide lease.
    #[cfg(unix)]
    pub fn duplicate_lock_fds(&self) -> Result<Vec<std::os::fd::OwnedFd>, AdapterError> {
        let files = self.files.as_ref().ok_or_else(|| {
            AdapterError::not_supported("interaction lease descriptor inheritance")
        })?;
        let mut fds = Vec::with_capacity(1 + files.app_locks.len());
        fds.push(files.primary.duplicate_inheritable()?);
        for lock in &files.app_locks {
            fds.push(lock.duplicate_inheritable()?);
        }
        Ok(fds)
    }

    #[cfg(unix)]
    fn from_file_lock(
        deadline: Deadline,
        file_guard: FileLock,
        process_guard: crate::session::process_lease_guard::ProcessLeaseGuard,
    ) -> Self {
        let contention_count = file_guard
            .contention_count()
            .saturating_add(process_guard.contention_count());
        Self {
            _guard: None,
            files: Some(UnixHeld {
                app_locks: Vec::new(),
                primary: Arc::new(file_guard),
                inheritable: true,
            }),
            _process_guard: Some(Arc::new(process_guard)),
            deadline,
            contention_count,
        }
    }

    #[cfg(unix)]
    fn from_app_lease(
        deadline: Deadline,
        primary: FileLock,
        app_locks: Vec<FileLock>,
        process_guard: crate::session::process_lease_guard::ProcessLeaseGuard,
    ) -> Self {
        let mut contention_count = primary
            .contention_count()
            .saturating_add(process_guard.contention_count());
        let app_locks = app_locks
            .into_iter()
            .map(|lock| {
                contention_count = contention_count.saturating_add(lock.contention_count());
                Arc::new(lock)
            })
            .collect();
        Self {
            _guard: None,
            files: Some(UnixHeld {
                app_locks,
                primary: Arc::new(primary),
                inheritable: false,
            }),
            _process_guard: Some(Arc::new(process_guard)),
            deadline,
            contention_count,
        }
    }

    /// A guarded lease that reports the contention it earned rather than a
    /// hardcoded zero, so a platform lease that queued behind another holder
    /// before it acquired reports that queueing in the success-path envelope
    /// (`ref_action_poll_state.rs`'s `lease_contention_count`) instead of
    /// understating it.
    pub fn guarded_with_contention(
        deadline: Deadline,
        guard: impl Send + Sync + 'static,
        contention_count: u64,
    ) -> Self {
        Self {
            _guard: Some(Arc::new(guard)),
            #[cfg(unix)]
            files: None,
            #[cfg(unix)]
            _process_guard: None,
            deadline,
            contention_count,
        }
    }
}

#[cfg(unix)]
pub fn acquire_unix_interaction_lease(
    deadline: Deadline,
) -> Result<InteractionLease, AdapterError> {
    acquire_unix_interaction_lease_for(
        &crate::session::lease_scope::InteractionLeaseScope::global(),
        deadline,
    )
}

#[cfg(unix)]
pub fn acquire_unix_interaction_lease_for(
    scope: &crate::session::lease_scope::InteractionLeaseScope,
    deadline: Deadline,
) -> Result<InteractionLease, AdapterError> {
    acquire_scoped_at(scope, deadline, std::path::Path::new("/tmp"))
}

#[cfg(unix)]
pub fn adopt_inherited_unix_interaction_lease(
    raw_fd: std::os::fd::RawFd,
    deadline: Deadline,
) -> Result<InteractionLease, AdapterError> {
    adopt_inherited_unix_interaction_lease_at(raw_fd, deadline, std::path::Path::new("/tmp"))
}

#[cfg(unix)]
fn interaction_lock_path_at(root: &std::path::Path) -> Result<PathBuf, AdapterError> {
    let uid = unsafe { libc::geteuid() };
    let directory = root.join(format!("agent-desktop-{uid}"));
    ensure_unix_runtime_directory(&directory, uid)?;
    Ok(directory.join("interaction.lock"))
}

#[cfg(unix)]
fn acquire_scoped_at(
    scope: &crate::session::lease_scope::InteractionLeaseScope,
    deadline: Deadline,
    root: &std::path::Path,
) -> Result<InteractionLease, AdapterError> {
    let stems = scope.app_lock_stems();
    let process_guard = if stems.is_empty() {
        crate::session::process_lease_guard::ProcessLeaseGuard::acquire(deadline)?
    } else {
        crate::session::process_lease_guard::ProcessLeaseGuard::acquire_apps(&stems, deadline)?
    };
    let directory = interaction_lock_path_at(root)?
        .parent()
        .ok_or_else(|| AdapterError::internal("interaction lease path has no parent directory"))?
        .to_path_buf();
    let global_path = directory.join("interaction.lock");
    if stems.is_empty() {
        let lock = FileLock::acquire(&global_path, deadline, "desktop interaction lease")?;
        return Ok(InteractionLease::from_file_lock(
            deadline,
            lock,
            process_guard,
        ));
    }
    let primary = FileLock::acquire_shared(&global_path, deadline, "desktop interaction lease")?;
    let uid = unsafe { libc::geteuid() };
    let apps_dir = directory.join("apps");
    ensure_unix_runtime_directory(&apps_dir, uid)?;
    let mut app_locks = Vec::with_capacity(stems.len());
    for stem in &stems {
        let path = apps_dir.join(format!("{stem}.lock"));
        app_locks.push(FileLock::acquire(
            &path,
            deadline,
            "desktop application lease",
        )?);
    }
    Ok(InteractionLease::from_app_lease(
        deadline,
        primary,
        app_locks,
        process_guard,
    ))
}

#[cfg(all(unix, test))]
fn acquire_unix_interaction_lease_at(
    deadline: Deadline,
    root: &std::path::Path,
) -> Result<InteractionLease, AdapterError> {
    acquire_scoped_at(
        &crate::session::lease_scope::InteractionLeaseScope::global(),
        deadline,
        root,
    )
}

#[cfg(unix)]
fn adopt_inherited_unix_interaction_lease_at(
    raw_fd: std::os::fd::RawFd,
    deadline: Deadline,
    root: &std::path::Path,
) -> Result<InteractionLease, AdapterError> {
    let process_guard = crate::session::process_lease_guard::ProcessLeaseGuard::acquire(deadline)?;
    let uid = unsafe { libc::geteuid() };
    let directory = root.join(format!("agent-desktop-{uid}"));
    validate_unix_runtime_directory(&directory, uid)?;
    let path = directory.join("interaction.lock");
    let lock = FileLock::adopt_inherited(
        raw_fd,
        &path,
        deadline,
        "inherited desktop interaction lease",
    )?;
    Ok(InteractionLease::from_file_lock(
        deadline,
        lock,
        process_guard,
    ))
}

#[cfg(unix)]
fn ensure_unix_runtime_directory(
    directory: &std::path::Path,
    uid: u32,
) -> Result<(), AdapterError> {
    use std::os::unix::fs::DirBuilderExt;

    match std::fs::symlink_metadata(directory) {
        Ok(_) => validate_unix_runtime_directory(directory, uid),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            match std::fs::DirBuilder::new().mode(0o700).create(directory) {
                Ok(()) => validate_unix_runtime_directory(directory, uid),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                    validate_unix_runtime_directory(directory, uid)
                }
                Err(error) => Err(runtime_io_error(error)),
            }
        }
        Err(error) => Err(runtime_io_error(error)),
    }
}

#[cfg(unix)]
fn validate_unix_runtime_directory(
    directory: &std::path::Path,
    uid: u32,
) -> Result<(), AdapterError> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    let metadata = std::fs::symlink_metadata(directory).map_err(runtime_io_error)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() || metadata.uid() != uid {
        return Err(AdapterError::new(
            ErrorCode::Internal,
            "Interaction runtime directory is not a private user-owned directory",
        ));
    }
    if metadata.permissions().mode() & 0o077 != 0 {
        std::fs::set_permissions(directory, std::fs::Permissions::from_mode(0o700))
            .map_err(runtime_io_error)?;
    }
    Ok(())
}

#[cfg(unix)]
fn runtime_io_error(error: std::io::Error) -> AdapterError {
    AdapterError::new(ErrorCode::Internal, error.to_string())
}

#[cfg(all(unix, test))]
pub(crate) fn exclusive_test_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[cfg(test)]
#[path = "interaction_lease_tests.rs"]
mod tests;

#[cfg(test)]
#[path = "scoped_lease_tests.rs"]
mod scoped_tests;
