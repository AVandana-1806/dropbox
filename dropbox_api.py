    def finalize(self) -> Dict[str, Any]:
        """Build the execution summary shown as the state machine's final output."""
        execution_id = self.event.get("execution_id")

        state = self.release_api.get_execution_state(execution_id)
        results = state.get("results", [])
        blocked = state.get("blocked_apps", [])

        summary = self._tally(results)
        by_env = self._tally_by_env(results)

        if summary["failure"] or summary["rollback"]:
            status = "COMPLETED_WITH_FAILURES"
        else:
            status = "COMPLETED"

        return {
            "execution_id": execution_id,
            "status": status,
            "summary": summary,
            "environments": by_env,
            "blocked_apps": blocked,
        }

    @staticmethod
    def _tally(results: list) -> Dict[str, int]:
        counts = {"total": 0, "success": 0, "failure": 0, "rollback": 0, "skipped": 0}
        for r in results:
            counts["total"] += 1
            key = r["Status"].lower()
            counts[key] = counts.get(key, 0) + 1
        return counts

    @staticmethod
    def _tally_by_env(results: list) -> Dict[str, Dict[str, int]]:
        envs: Dict[str, Dict[str, int]] = {}
        for r in results:
            env = envs.setdefault(
                r["Env"], {"success": 0, "failure": 0, "rollback": 0, "skipped": 0}
            )
            key = r["Status"].lower()
            env[key] = env.get(key, 0) + 1
        return envs
