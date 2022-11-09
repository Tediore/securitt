# securitt
**securitt** is an MQTT-based security system meant to be used with zigbee2mqtt. It allows you to configure a security system in YAML that handles input from Zigbee keypads and key fobs (and Home Assistant, if desired) and also sound Zigbee sirens when the alarm is triggered. I created this solely because I thought it would be fun to write a security system from scratch. It was more fun than I thought it would be, surprisingly. I don't know if anyone would actually use this, but I wanted to share it anyway.

**Configuration**
See config.yaml for an example config file with all configuration options and comments explaining most options.

**Reloading configuration without restart**
You can reload all configuration options (except MQTT settings and log level) without restarting by publishing a blank payload to `base_topic`/reload_config

**Docker-compose**
Example docker-compose.yaml service entry:
```yaml
  securitt:
    container_name: securitt
    image: tediore/securitt:latest
    volumes:
    - /path/to/configuration:/app/data
    environment:
    - TZ=America/Chicago
    restart: unless-stopped
```

**Docker run**
Example `docker run` command:
```
docker run --name securitt \
-v /path/to/configuration:/app/data \
-e TZ=America/Chicago \
tediore/securitt:latest
```