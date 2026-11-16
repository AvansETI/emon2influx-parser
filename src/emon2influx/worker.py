"""The processing loop: takes messages off the queue, runs them through the pipeline and hands
the points to the sink."""

import queue
import threading

from .pipeline import Pipeline
from .sink.sink import InfluxSink
from .source.mqtt import Message
from .summary import SummaryReporter


class Worker:
    def __init__(
        self,
        messages: "queue.Queue[Message]",
        pipeline: Pipeline,
        sink: InfluxSink,
        summary: SummaryReporter,
    ) -> None:
        self.messages = messages
        self.pipeline = pipeline
        self.sink = sink
        self.summary = summary

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                raw, received_at = self.messages.get(timeout=1.0)
            except queue.Empty:
                pass
            else:
                self.sink.add(self.pipeline.process(raw, received_at))
            self.summary.maybe_report()

    def drain(self) -> int:
        """Processes what was already received; returns the number of messages."""
        count = 0
        while True:
            try:
                raw, received_at = self.messages.get_nowait()
            except queue.Empty:
                return count
            self.sink.add(self.pipeline.process(raw, received_at))
            count += 1
