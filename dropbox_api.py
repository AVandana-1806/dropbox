        groups = CloudWatchBatchTransformer._group(envelope, events)
        if len(groups) == 1:
            # One group means no progress: the chunk would be the input, so it
            # would come back oversized and split into itself forever. Halve by
            # count instead - each pass halves again until the pieces fit.
            middle = len(events) // 2
            groups = [events[:middle], events[middle:]]

        chunks = [
            CloudWatchBatchTransformer._envelope(envelope, group) for group in groups
        ]


    @staticmethod
    def _group(envelope: dict, events: list[dict]) -> list[list[dict]]:
        # Two budgets, whichever binds first: raw envelope bytes keep the
        # gzipped record under Firehose's per-record limit, transformed bytes
        # keep the re-processed result under the response limit. Every HEC line
        # repeats the log group, stream and filter names, so a batch of many
        # tiny events is far larger coming out than going in.
        hec_overhead = (
            HEC_LINE_OVERHEAD_BYTES
            + len(str(envelope.get("logGroup", "")).encode())
            + len(str(envelope.get("logStream", "")).encode())
            + len(",".join(envelope.get("subscriptionFilters", [])).encode())
        )
        groups: list[list[dict]] = []
        current: list[dict] = []
        raw = transformed = 0
        for event in events:
            message = len(str(event.get("message", "")).encode())
            event_raw = message + EVENT_OVERHEAD_BYTES
            event_transformed = message + hec_overhead
            over_raw = raw + event_raw > REINGEST_CHUNK_BYTES
            over_transformed = (
                transformed + event_transformed > TRANSFORMED_CHUNK_BYTES
            )
            if current and (over_raw or over_transformed):
                groups.append(current)
                current, raw, transformed = [], 0, 0
            current.append(event)
            raw += event_raw
            transformed += event_transformed
        if current:
            groups.append(current)
        return groups
