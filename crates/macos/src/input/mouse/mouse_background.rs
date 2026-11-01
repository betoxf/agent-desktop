use agent_desktop_core::{
    AdapterError, Deadline, ErrorCode, MouseButton, MouseEvent, MouseEventKind, OperationReport,
    Point, Rect,
};

const PRESS: &str = "AXPress";
const PRESS_SEARCH_DEPTH: usize = 4;

/// A plain click can go through the element under the point, which every window
/// accepts. Anything richer only exists as raw input.
fn can_press_element(event: &MouseEvent) -> bool {
    matches!(event.button, MouseButton::Left)
        && event.modifiers.is_empty()
        && matches!(event.kind, MouseEventKind::Click { count: 1 })
}

/// Records come front to back, so the first hit is the app's topmost window here.
fn window_at<'a, T>(
    windows: &'a [T],
    bounds: impl Fn(&T) -> &Rect,
    point: &Point,
) -> Option<&'a T> {
    windows.iter().find(|window| {
        let rect = bounds(window);
        point.x >= rect.x
            && point.y >= rect.y
            && point.x < rect.x + rect.width
            && point.y < rect.y + rect.height
    })
}

#[cfg(target_os = "macos")]
mod imp {
    use super::*;
    use crate::actions::ax_perform::{advertises_action, perform_observed_action};
    use crate::actions::chain::chain_delivery::DeliveryOutcome;
    use crate::system::window::cg_window::{WindowRecordScope, window_records_until};
    use crate::tree::{AXElement, element_for_pid, properties::ax_ipc};
    use core_graphics::event::{CGEvent, CGEventType, CGMouseButton, EventField};
    use core_graphics::event_source::{CGEventSource, CGEventSourceStateID};
    use core_graphics::geometry::CGPoint;
    use foreign_types::ForeignType;
    use std::time::Instant;

    /// `CGEventField` 51: the window AppKit routes the event to. Without it the
    /// event reaches the app with no window, and no view ever sees it.
    const EVENT_WINDOW_NUMBER: u32 = 51;

    type SetWindowLocation = unsafe extern "C" fn(*mut std::ffi::c_void, CGPoint);

    /// Exported by CoreGraphics but not in its headers, so it is found at run time.
    /// AppKit hit-tests an event by this location, and a raw `CGEvent` otherwise
    /// carries the location of the real pointer's window.
    fn set_window_location() -> Option<SetWindowLocation> {
        let symbol =
            unsafe { libc::dlsym(libc::RTLD_DEFAULT, c"CGEventSetWindowLocation".as_ptr()) };
        (!symbol.is_null())
            .then(|| unsafe { std::mem::transmute::<*mut libc::c_void, SetWindowLocation>(symbol) })
    }

    pub(crate) fn background_click(
        pid: i32,
        event: MouseEvent,
        deadline: Deadline,
    ) -> Result<OperationReport, AdapterError> {
        crate::input::mouse::validate_point(&event.point)?;
        let MouseEventKind::Click { count } = event.kind else {
            return Err(AdapterError::not_supported("background pointer gesture"));
        };
        if can_press_element(&event) && press_element_at(pid, &event.point, deadline)? {
            return Ok(OperationReport::background_click(&event, true));
        }
        let until = Instant::now() + deadline.remaining();
        let windows = window_records_until(until, WindowRecordScope::Pid(pid))?;
        let window =
            window_at(&windows, |record| &record.bounds, &event.point).ok_or_else(|| {
                AdapterError::new(
                    ErrorCode::ElementNotFound,
                    "No window of this app is on screen at that point",
                )
                .with_suggestion(
                    "Run list-windows; a hidden or minimized app has no window to click",
                )
            })?;
        let location = CGPoint::new(
            event.point.x - window.bounds.x,
            event.point.y - window.bounds.y,
        );
        post_click(pid, window.window_number, location, &event, count)?;
        Ok(OperationReport::background_click(&event, false))
    }

    fn press_element_at(pid: i32, point: &Point, deadline: Deadline) -> Result<bool, AdapterError> {
        let app = element_for_pid(pid);
        let (status, raw) =
            ax_ipc::element_at_position(&app, (point.x as f32, point.y as f32), deadline);
        if status != accessibility_sys::kAXErrorSuccess || raw.is_null() {
            return Ok(false);
        }
        let mut element = AXElement(raw);
        for _ in 0..PRESS_SEARCH_DEPTH {
            if advertises_action(&element, PRESS, deadline) == Some(true) {
                let outcome = perform_observed_action(&element, PRESS, deadline)?;
                return Ok(!matches!(outcome, DeliveryOutcome::NotDelivered));
            }
            match crate::actions::scroll_read::element(&element, "AXParent", deadline) {
                Ok(Some(parent)) => element = parent,
                _ => return Ok(false),
            }
        }
        Ok(false)
    }

    fn post_click(
        pid: i32,
        window: i64,
        location: CGPoint,
        event: &MouseEvent,
        count: u32,
    ) -> Result<(), AdapterError> {
        let set_location = set_window_location()
            .ok_or_else(|| AdapterError::not_supported("background pointer input on this macOS"))?;
        let (down, up, button) = match event.button {
            MouseButton::Left => (
                CGEventType::LeftMouseDown,
                CGEventType::LeftMouseUp,
                CGMouseButton::Left,
            ),
            MouseButton::Right => (
                CGEventType::RightMouseDown,
                CGEventType::RightMouseUp,
                CGMouseButton::Right,
            ),
            MouseButton::Middle => (
                CGEventType::OtherMouseDown,
                CGEventType::OtherMouseUp,
                CGMouseButton::Center,
            ),
        };
        let screen = CGPoint::new(event.point.x, event.point.y);
        let flags = crate::input::mouse::event_flags(&event.modifiers);
        for click in 1..=i64::from(count) {
            for (kind, pressure) in [(down, 1.0), (up, 0.0)] {
                let source = CGEventSource::new(CGEventSourceStateID::Private)
                    .map_err(|()| AdapterError::internal("CGEventSource::new failed"))?;
                let raw = CGEvent::new_mouse_event(source, kind, screen, button)
                    .map_err(|()| AdapterError::internal("CGEvent::new_mouse_event failed"))?;
                raw.set_flags(flags);
                raw.set_integer_value_field(EventField::MOUSE_EVENT_CLICK_STATE, click);
                raw.set_double_value_field(EventField::MOUSE_EVENT_PRESSURE, pressure);
                raw.set_integer_value_field(EVENT_WINDOW_NUMBER, window);
                raw.set_integer_value_field(
                    EventField::MOUSE_EVENT_WINDOW_UNDER_MOUSE_POINTER,
                    window,
                );
                raw.set_integer_value_field(
                    EventField::MOUSE_EVENT_WINDOW_UNDER_MOUSE_POINTER_THAT_CAN_HANDLE_THIS_EVENT,
                    window,
                );
                unsafe { set_location(raw.as_ptr().cast(), location) };
                raw.post_to_pid(pid);
            }
        }
        Ok(())
    }
}

#[cfg(not(target_os = "macos"))]
mod imp {
    use super::*;

    pub(crate) fn background_click(
        _pid: i32,
        _event: MouseEvent,
        _deadline: Deadline,
    ) -> Result<OperationReport, AdapterError> {
        Err(AdapterError::not_supported("background_click"))
    }
}

pub(crate) use imp::background_click;

#[cfg(test)]
mod tests {
    use super::*;

    fn click(button: MouseButton, count: u32) -> MouseEvent {
        MouseEvent {
            kind: MouseEventKind::Click { count },
            point: Point { x: 5.0, y: 5.0 },
            button,
            modifiers: Vec::new(),
        }
    }

    fn rect(x: f64, width: f64) -> Rect {
        Rect {
            x,
            y: 0.0,
            width,
            height: 10.0,
        }
    }

    #[test]
    fn only_a_plain_left_click_can_press_an_element() {
        assert!(can_press_element(&click(MouseButton::Left, 1)));
        assert!(!can_press_element(&click(MouseButton::Left, 2)));
        assert!(!can_press_element(&click(MouseButton::Right, 1)));
        let mut held = click(MouseButton::Left, 1);
        held.modifiers.push(agent_desktop_core::Modifier::Shift);
        assert!(!can_press_element(&held));
    }

    #[test]
    fn the_topmost_window_at_the_point_wins() {
        let windows = [rect(100.0, 10.0), rect(0.0, 10.0), rect(0.0, 20.0)];
        let point = Point { x: 5.0, y: 5.0 };
        let found = window_at(&windows, |window| window, &point);
        assert_eq!(found.map(|window| window.width), Some(10.0));
        let outside = Point { x: 50.0, y: 5.0 };
        assert!(window_at(&windows, |window| window, &outside).is_none());
    }
}
