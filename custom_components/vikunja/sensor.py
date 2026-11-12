"""Shieldxx fork: "due soon" sensor for notifications and small dashboard summaries.

One sensor per connection. State = number of open tasks that are overdue or due within
the next 7 days; attributes list those tasks (title, project, due date, flags, link).
The Vikunja token never leaves the integration: no rest sensor or secret is needed.
Refreshes every 5 minutes and right after any Task Hub dashboard action.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .automation import AUTOMATION_EVENT
from .const import DOMAIN, LOGGER

WINDOW_DAYS = 7
NO_DUE = "0001-01-01"


async def _fetch_due(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    data = hass.data[DOMAIN][entry_id]
    api = data["api"]
    try:
        response = await api._request(  # pyvikunja has no filtered task list helper
            "GET",
            "/tasks",
            params={
                "filter": f"done = false && due_date < now+{WINDOW_DAYS}d",
                "sort_by": "due_date",
                "order_by": "asc",
                "per_page": 100,
            },
        )
        projects = {project.id: project.title for project in await api.get_projects()}
    except Exception as err:  # pyvikunja wraps transport errors in APIError
        raise UpdateFailed(f"Unable to load due Vikunja tasks: {err}") from err

    now = dt_util.now()
    today = now.date()
    tasks = []
    for raw in response.get("data") or []:
        due_raw = str(raw.get("due_date") or "")
        if not due_raw or due_raw.startswith(NO_DUE):
            continue
        due = dt_util.parse_datetime(due_raw)
        if due is None:
            continue
        due_local = dt_util.as_local(due)
        tasks.append(
            {
                "id": raw.get("id"),
                "title": raw.get("title") or "",
                "project": projects.get(raw.get("project_id"), ""),
                "due": due_local.isoformat(),
                "due_date": due_local.date().isoformat(),
                "overdue": due_local.date() < today,
                "due_today": due_local.date() == today,
                "due_tomorrow": due_local.date() == today + timedelta(days=1),
                "url": f"{api.web_ui_link.rstrip('/')}/tasks/{raw.get('id')}",
            }
        )
    return {
        "tasks": tasks,
        "overdue": sum(1 for t in tasks if t["overdue"]),
        "today": sum(1 for t in tasks if t["due_today"]),
        "tomorrow": sum(1 for t in tasks if t["due_tomorrow"]),
        "updated": now.isoformat(),
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the due-soon sensor for one connection."""
    coordinator = DataUpdateCoordinator(
        hass,
        LOGGER,
        config_entry=entry,
        name=f"{DOMAIN} due soon",
        update_method=lambda: _fetch_due(hass, entry.entry_id),
        update_interval=timedelta(minutes=5),
    )
    await coordinator.async_config_entry_first_refresh()
    async_add_entities([VikunjaDueSoonSensor(entry, coordinator)])


class VikunjaDueSoonSensor(CoordinatorEntity, SensorEntity):
    """Open tasks that are overdue or due within the next week."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:calendar-alert"
    _attr_native_unit_of_measurement = "tasks"

    def __init__(self, entry: ConfigEntry, coordinator: DataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._entry_id = entry.entry_id
        self._attr_name = "Vikunja tasks due soon"
        self._attr_unique_id = f"{entry.entry_id}_due_soon"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _refresh(event: Event) -> None:
            if event.data.get("entry_id") in (None, self._entry_id):
                self.hass.async_create_task(self.coordinator.async_request_refresh())

        self.async_on_remove(self.hass.bus.async_listen(AUTOMATION_EVENT, _refresh))

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("tasks", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {
            "tasks": data.get("tasks", []),
            "overdue": data.get("overdue", 0),
            "today": data.get("today", 0),
            "tomorrow": data.get("tomorrow", 0),
            "window_days": WINDOW_DAYS,
            "updated": data.get("updated"),
        }
