Subject: cares-snowflake-connector rename (blocking mTLS)

Hi <Developer>,

The cares-snowflake-connector doesn't load Snowflake. It just moves messages from <topic A> to <topic B>. Our other Kafka Connect cluster on ECS does the actual Snowflake load.

The name clashes with that one, and I need the right name before I can set up the mTLS cert, ACLs and consumer group. This is blocking the mTLS work.

Can you confirm what it does (topics, any transforms) and rename it to match? Also, a new name means a new consumer group, so let me know where it should start from.

Thanks,
Adarsh
