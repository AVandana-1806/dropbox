        p.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        p.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        // Pass any other "cares.producer.*" property straight to the producer (e.g. security.protocol, ssl.*).
        p.putAll(cfg.originalsWithPrefix("cares.producer."));

        this.producer = new KafkaProducer<>(p);
