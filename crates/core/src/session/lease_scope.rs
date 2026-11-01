use crate::ProcessId;

/// Which concurrent desktop actions this lease allows.
///
/// `Global` is the machine-wide lock. `App` names one application. An app
/// lease holds the machine-wide lock in shared mode and then its own locks,
/// so a later machine-wide lease waits for it and a different application
/// does not.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InteractionLeaseScope {
    Global,
    App {
        bundle_id: Option<String>,
        pid: Option<ProcessId>,
    },
}

impl InteractionLeaseScope {
    pub fn global() -> Self {
        Self::Global
    }

    /// An application lock when `bundle_id` or a non-zero `pid` is present.
    /// With neither, the caller has no target and takes the machine-wide lock.
    pub fn app(bundle_id: Option<&str>, pid: Option<ProcessId>) -> Self {
        let bundle_id = bundle_id.and_then(normalize_bundle);
        let pid = pid.filter(|pid| pid.get() != 0);
        if bundle_id.is_none() && pid.is_none() {
            Self::Global
        } else {
            Self::App { bundle_id, pid }
        }
    }

    /// Real pointer, keyboard, or focus-stealing input takes the machine-wide lock.
    pub fn for_input(real_input: bool, bundle_id: Option<&str>, pid: Option<ProcessId>) -> Self {
        if real_input {
            Self::Global
        } else {
            Self::app(bundle_id, pid)
        }
    }

    pub fn is_global(&self) -> bool {
        matches!(self, Self::Global)
    }

    /// Lock-file stems in acquisition order: bundle id, then process id.
    pub fn app_lock_stems(&self) -> Vec<String> {
        let Self::App { bundle_id, pid } = self else {
            return Vec::new();
        };
        let mut stems = Vec::new();
        if let Some(bundle_id) = bundle_id {
            stems.push(format!("b-{}", file_token(bundle_id)));
        }
        if let Some(pid) = pid {
            stems.push(format!("p-{}", pid.get()));
        }
        stems
    }
}

fn normalize_bundle(raw: &str) -> Option<String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_ascii_lowercase())
    }
}

fn file_token(value: &str) -> String {
    let mut safe = String::new();
    let mut changed = false;
    for character in value.chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '.' | '-' | '_') {
            safe.push(character);
        } else {
            changed = true;
            safe.push('_');
        }
    }
    if safe.is_empty() || safe == "." || safe == ".." {
        return format!("k-{:016x}", fnv1a(value.as_bytes()));
    }
    if changed || safe.len() > 64 {
        let prefix: String = safe.chars().take(32).collect();
        return format!("{prefix}-{:016x}", fnv1a(value.as_bytes()));
    }
    safe
}

fn fnv1a(bytes: &[u8]) -> u64 {
    let mut hash = 0xcbf29ce484222325_u64;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100_0000_01b3);
    }
    hash
}

#[cfg(test)]
#[path = "lease_scope_tests.rs"]
mod tests;
