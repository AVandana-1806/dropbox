    @staticmethod
    def _version_filters(
        application_name: Optional[str],
        environment_name: Optional[str],
        manifest_sync: Optional[bool],
    ) -> list:
        conditions = []
        if application_name:
            conditions.append(Application.name == application_name)
        if environment_name:
            conditions.append(Environment.name == environment_name)
        if manifest_sync is not None:
            conditions.append(ApplicationVersion.manifest_sync == manifest_sync)
        return conditions

    @staticmethod
    def _latest_only_join(query, conditions: list):
        latest = (
            select(
                ApplicationVersion.application_id,
                ApplicationVersion.environment_id,
                func.max(ApplicationVersion.deployed_at).label("max_deployed"),
            )
            .join(Application, Application.id == ApplicationVersion.application_id)
            .join(Environment, Environment.id == ApplicationVersion.environment_id)
            .where(*conditions)
            .group_by(
                ApplicationVersion.application_id,
                ApplicationVersion.environment_id,
            )
            .subquery()
        )
        return query.join(
            latest,
            and_(
                ApplicationVersion.application_id == latest.c.application_id,
                ApplicationVersion.environment_id == latest.c.environment_id,
                ApplicationVersion.deployed_at == latest.c.max_deployed,
            ),
        )

    @staticmethod
    async def list_application_versions(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 10,
        application_name: Optional[str] = None,
        environment_name: Optional[str] = None,
        latest_only: bool = False,
        manifest_sync: Optional[bool] = None,
        expand: Optional[str] = None,
    ) -> list[ApplicationVersion]:
        """List application versions with optional filtering and metadata expansion."""
        include_metadata = expand == "metadata"

        query = (
            select(ApplicationVersion)
            .join(Application, Application.id == ApplicationVersion.application_id)
            .join(Environment, Environment.id == ApplicationVersion.environment_id)
        )

        if include_metadata:
            query = query.join(
                RepositoryModel, RepositoryModel.id == Application.repository_id
            ).add_columns(
                Application.name.label("application_name"),
                Environment.name.label("environment_name"),
                RepositoryModel.name.label("repository_name"),
            )

        conditions = ApplicationRepository._version_filters(
            application_name, environment_name, manifest_sync
        )
        query = query.where(*conditions)

        if latest_only:
            query = ApplicationRepository._latest_only_join(query, conditions)

        query = (
            query.order_by(ApplicationVersion.deployed_at.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await db.execute(query)

        if not include_metadata:
            return list(result.scalars().all())

        versions = []
        for av, app_name, env_name, repo_name in result.all():
            av.application_name = app_name
            av.environment_name = env_name
            av.repository_name = repo_name
            versions.append(av)
        return versions
