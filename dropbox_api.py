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

    query = select(ApplicationVersion)

    if include_metadata:
        query = query.options(
            selectinload(ApplicationVersion.application).selectinload(Application.repository),
            selectinload(ApplicationVersion.environment),
        )

    has_app_join = False
    has_env_join = False

    if application_name:
        query = query.join(Application, Application.id == ApplicationVersion.application_id)
        has_app_join = True
        query = query.where(Application.name == application_name)

    if environment_name:
        query = query.join(Environment, Environment.id == ApplicationVersion.environment_id)
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
            latest_subq = latest_subq.where(ApplicationVersion.manifest_sync == manifest_sync)

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

    query = query.order_by(ApplicationVersion.deployed_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    return list(result.scalars().all())
