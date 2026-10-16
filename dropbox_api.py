L117–140: The worst case is 12 × 5s per certificate, run in sequence. A sweep over several pending certificates can hit the Lambda timeout. Make sure the timeout is sized for this, or cap the sweep.
L140: The code sleeps after the final attempt before raising, which wastes 5s. Skip the sleep when attempt == self.max_attempts.
