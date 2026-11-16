"""MQTT source: subscribes in on_connect (so a reconnect resubscribes), reconnects by itself and
only queues raw payloads, so the network thread never blocks (doc/architecture.md §6)."""

import contextlib
import logging
import queue
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.reasoncodes import ReasonCode

from ..config import Settings

log = logging.getLogger("emon2influx")

Message = tuple[bytes, datetime]


class MqttSource:
    def __init__(
        self,
        settings: Settings,
        messages: "queue.Queue[Message]",
        on_drop: Callable[[], None],
    ) -> None:
        self.settings = settings
        self.messages = messages
        self.on_drop = on_drop
        self.client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
            clean_session=settings.mqtt_clean_session,
        )
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        if settings.mqtt_tls:
            self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self) -> None:
        log.info(
            "connecting to MQTT broker %s:%d as %s",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
            self.settings.mqtt_client_id,
        )
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

    def is_connected(self) -> bool:
        return self.client.is_connected()

    def _on_connect(
        self, client: mqtt.Client, userdata: Any, flags: Any, reason: ReasonCode, properties: Any
    ) -> None:
        if reason.is_failure:
            log.error("MQTT connection refused: %s", reason)
            return
        client.subscribe(self.settings.mqtt_topic, qos=self.settings.mqtt_qos)
        log.info("connected to MQTT broker; subscribed to %s", self.settings.mqtt_topic)

    def _on_disconnect(
        self, client: mqtt.Client, userdata: Any, flags: Any, reason: ReasonCode, properties: Any
    ) -> None:
        if reason.is_failure:
            log.error("MQTT connection lost (%s); reconnecting", reason)
        else:
            log.info("disconnected from MQTT broker")

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        item = (bytes(message.payload), datetime.now(UTC))
        try:
            self.messages.put_nowait(item)
        except queue.Full:
            # Drop the oldest so the newest data keeps flowing; counted in the summary.
            with contextlib.suppress(queue.Empty):
                self.messages.get_nowait()
            self.on_drop()
            try:
                self.messages.put_nowait(item)
            except queue.Full:
                self.on_drop()
