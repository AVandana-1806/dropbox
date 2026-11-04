Subject: cares-snowflake-connector rename (blocking mTLS)

Hi <Developer>,

The cares-snowflake-connector doesn't load Snowflake. It just moves messages from <topic A> to <topic B>. Our other Kafka Connect cluster on ECS does the actual Snowflake load.

The name clashes with that one, and it's blocking the mTLS work.

Can you confirm what it does and rename it to match?

Thanks,
Adarsh
