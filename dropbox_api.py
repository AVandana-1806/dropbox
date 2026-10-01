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
