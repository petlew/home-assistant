"""Offer numeric state listening automation rules."""
import logging

import voluptuous as vol

from homeassistant import exceptions
from homeassistant.const import (
    CONF_ABOVE,
    CONF_BELOW,
    CONF_ENTITY_ID,
    CONF_FOR,
    CONF_PLATFORM,
    CONF_VALUE_TEMPLATE,
)
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers import condition, config_validation as cv, template
from homeassistant.helpers.event import async_track_same_state, async_track_state_change

# mypy: allow-incomplete-defs, allow-untyped-calls, allow-untyped-defs
# mypy: no-check-untyped-defs

TRIGGER_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(CONF_PLATFORM): "numeric_state",
            vol.Required(CONF_ENTITY_ID): cv.entity_ids,
            vol.Optional(CONF_BELOW): vol.Coerce(float),
            vol.Optional(CONF_ABOVE): vol.Coerce(float),
            vol.Optional(CONF_VALUE_TEMPLATE): cv.template,
            vol.Optional(CONF_FOR): vol.Any(
                vol.All(cv.time_period, cv.positive_timedelta),
                cv.template,
                cv.template_complex,
            ),
        }
    ),
    cv.has_at_least_one_key(CONF_BELOW, CONF_ABOVE),
)

_LOGGER = logging.getLogger(__name__)


async def async_attach_trigger(
    hass, config, action, automation_info, *, platform_type="numeric_state"
) -> CALLBACK_TYPE:
    """Listen for state changes based on configuration."""
    entity_id = config.get(CONF_ENTITY_ID)
    below = config.get(CONF_BELOW)
    above = config.get(CONF_ABOVE)
    time_delta = config.get(CONF_FOR)
    template.attach(hass, time_delta)
    value_template = config.get(CONF_VALUE_TEMPLATE)
    unsub_track_same = {}
    entities_triggered = set()
    period: dict = {}

    if value_template is not None:
        value_template.hass = hass

    @callback
    def check_numeric_state(entity, from_s, to_s):
        """Return True if criteria are now met."""
        if to_s is None:
            return False

        variables = {
            "trigger": {
                "platform": "numeric_state",
                "entity_id": entity,
                "below": below,
                "above": above,
            }
        }
        return condition.async_numeric_state(
            hass, to_s, below, above, value_template, variables
        )

    @callback
    def state_automation_listener(entity, from_s, to_s):
        """Listen for state changes and calls action."""

        @callback
        def call_action():
            """Call action with right context."""
            hass.async_run_job(
                action(
                    {
                        "trigger": {
                            "platform": platform_type,
                            "entity_id": entity,
                            "below": below,
                            "above": above,
                            "from_state": from_s,
                            "to_state": to_s,
                            "for": time_delta if not time_delta else period[entity],
                        }
                    },
                    context=to_s.context,
                )
            )

        matching = check_numeric_state(entity, from_s, to_s)

        if not matching:
            entities_triggered.discard(entity)
        elif entity not in entities_triggered:
            entities_triggered.add(entity)

            if time_delta:
                variables = {
                    "trigger": {
                        "platform": "numeric_state",
                        "entity_id": entity,
                        "below": below,
                        "above": above,
                    }
                }

                try:
                    if isinstance(time_delta, template.Template):
                        period[entity] = vol.All(cv.time_period, cv.positive_timedelta)(
                            time_delta.async_render(variables)
                        )
                    elif isinstance(time_delta, dict):
                        time_delta_data = {}
                        time_delta_data.update(
                            template.render_complex(time_delta, variables)
                        )
                        period[entity] = vol.All(cv.time_period, cv.positive_timedelta)(
                            time_delta_data
                        )
                    else:
                        period[entity] = time_delta
                except (exceptions.TemplateError, vol.Invalid) as ex:
                    _LOGGER.error(
                        "Error rendering '%s' for template: %s",
                        automation_info["name"],
                        ex,
                    )
                    entities_triggered.discard(entity)
                    return

                unsub_track_same[entity] = async_track_same_state(
                    hass,
                    period[entity],
                    call_action,
                    entity_ids=entity,
                    async_check_same_func=check_numeric_state,
                )
            else:
                call_action()

    unsub = async_track_state_change(hass, entity_id, state_automation_listener)

    @callback
    def async_remove():
        """Remove state listeners async."""
        unsub()
        for async_remove in unsub_track_same.values():
            async_remove()
        unsub_track_same.clear()

    return async_remove
