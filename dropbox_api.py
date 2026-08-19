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

        if include_metadata:
            query = (
                select(
                    ApplicationVersion,
                    Application.name.label("application_name"),
                    Environment.name.label("environment_name"),
                    Repository.name.label("repository_name"),
                )
                .join(Application, Application.id == ApplicationVersion.application_id)
                .join(Environment, Environment.id == ApplicationVersion.environment_id)
                .join(Repository, Repository.id == Application.repository_id)
            )
            has_app_join = True
            has_env_join = True
        else:
            query = select(ApplicationVersion)
            has_app_join = False
            has_env_join = False

        if application_name:
            if not has_app_join:
                query = query.join(
                    Application, Application.id == ApplicationVersion.application_id
                )
                has_app_join = True
            query = query.where(Application.name == application_name)

        if environment_name:
            if not has_env_join:
                query = query.join(
                    Environment, Environment.id == ApplicationVersion.environment_id
                )
                has_env_join = True
            query = query.where(Environment.name == environment_name)

        if manifest_sync is not None:
            query = query.where(ApplicationVersion.manifest_sync == manifest_sync)

        if latest_only:
            latest_subq = select(
                ApplicationVersion.application_id,
                ApplicationVersion.environment_id,
                func.max(ApplicationVersion.deployed_at).label("max_deployed"),
            )

            if application_name:
                latest_subq = latest_subq.join(
                    Application, Application.id == ApplicationVersion.application_id
                ).where(Application.name == application_name)

            if environment_name:
                latest_subq = latest_subq.join(
                    Environment, Environment.id == ApplicationVersion.environment_id
                ).where(Environment.name == environment_name)

            if manifest_sync is not None:
                latest_subq = latest_subq.where(
                    ApplicationVersion.manifest_sync == manifest_sync
                )

            latest_subq = latest_subq.group_by(
                ApplicationVersion.application_id,
                ApplicationVersion.environment_id,
            ).subquery()

            query = query.join(
                latest_subq,
                and_(
                    ApplicationVersion.application_id == latest_subq.c.application_id,
                    ApplicationVersion.environment_id == latest_subq.c.environment_id,
                    ApplicationVersion.deployed_at == latest_subq.c.max_deployed,
                ),
            )

        query = (
            query.order_by(ApplicationVersion.deployed_at.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await db.execute(query)

        if include_metadata:
            versions = []
            for av, app_name, env_name, repo_name in result.all():
                av.application_name = app_name
                av.environment_name = env_name
                av.repository_name = repo_name
                versions.append(av)
            return versions

        return list(result.scalars().all())

    @staticmethod
    async def update_version_metadata(
        db: AsyncSession,
        data: ApplicationVersionUpdate,
    ) -> ApplicationVersion:
        """
        Update a single application version's manifest_sync flag.

        Behavior:
        - If setting manifest_sync=True → ONLY update this row.
        - If setting manifest_sync=False:
            → mark this row as synced (manifest_synced_at=now())
            → clear ALL stale rows for same (application_id, environment_id)
            by setting manifest_sync=False.
        """
        row_q = await db.execute(
            select(ApplicationVersion)
            .join(Application, Application.id == ApplicationVersion.application_id)
            .join(Environment, Environment.id == ApplicationVersion.environment_id)
            .filter(
                Application.name == data.application_name,
                Environment.name == data.environment_name,
                ApplicationVersion.version == data.version,
            )
        )
        row = row_q.scalar_one_or_none()
        if not row:
            raise NotFoundException(
                f"ApplicationVersion not found for {data.application_name}/{data.environment_name}/{data.version}"
            )

        app_id = row.application_id
        env_id = row.environment_id

        if data.manifest_sync:
            row.manifest_sync = True
            row.manifest_synced_at = None
            await db.flush()
            await db.refresh(row)
            return row

        row.manifest_sync = False
        row.manifest_synced_at = func.now()

        await db.execute(
            ApplicationVersion.__table__.update()
            .where(
                ApplicationVersion.application_id == app_id,
                ApplicationVersion.environment_id == env_id,
                ApplicationVersion.id != row.id,
                ApplicationVersion.manifest_sync == True,
            )
            .values(manifest_sync=False)
        )

        await db.flush()
        await db.refresh(row)
        return row
