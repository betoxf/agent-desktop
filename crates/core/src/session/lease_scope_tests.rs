use super::*;

#[test]
fn a_missing_target_is_the_machine_lock() {
    assert!(InteractionLeaseScope::app(None, None).is_global());
    assert!(InteractionLeaseScope::app(Some("  "), None).is_global());
    assert!(InteractionLeaseScope::app(None, Some(ProcessId::new(0))).is_global());
}

#[test]
fn real_input_stays_on_the_machine_lock() {
    let scope =
        InteractionLeaseScope::for_input(true, Some("com.example.notes"), Some(ProcessId::new(42)));
    assert!(scope.is_global());
    assert!(scope.app_lock_stems().is_empty());
}

#[test]
fn bundle_identity_is_case_insensitive_and_ordered_before_pid() {
    let left = InteractionLeaseScope::app(Some(" Com.Example.Notes "), Some(ProcessId::new(7)));
    let right = InteractionLeaseScope::app(Some("com.example.notes"), Some(ProcessId::new(7)));
    assert_eq!(left, right);
    assert_eq!(
        left.app_lock_stems(),
        vec!["b-com.example.notes".to_string(), "p-7".to_string()]
    );
}

#[test]
fn pid_only_and_bundle_only_targets_keep_their_own_stem() {
    let pid_only = InteractionLeaseScope::app(None, Some(ProcessId::new(15)));
    assert_eq!(pid_only.app_lock_stems(), vec!["p-15".to_string()]);
    let bundle_only = InteractionLeaseScope::app(Some("com.example.notes"), None);
    assert_eq!(
        bundle_only.app_lock_stems(),
        vec!["b-com.example.notes".to_string()]
    );
}

#[test]
fn unsafe_bundle_characters_cannot_escape_the_lock_directory() {
    let scope = InteractionLeaseScope::app(Some("../com.example/notes"), Some(ProcessId::new(3)));
    let stems = scope.app_lock_stems();
    assert_eq!(stems.len(), 2);
    assert!(stems[0].starts_with("b-"));
    assert!(!stems[0].contains('/'));
    assert!(!stems[0].contains('\\'));
    let joined = std::path::Path::new("apps").join(&stems[0]);
    assert_eq!(joined.components().count(), 2);
    assert_eq!(stems[1], "p-3");
}
