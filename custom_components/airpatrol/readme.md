MVP for personal use: basic reader of sensors.

Airpatrol product: https://smartheat.airpatrol.eu

Usage:
1. add custom_components/airpatrol to your config folder
2. to configuration.yaml as platform:

```
airpatrol:
  username: <your airpatrol user>
  password: <your airpatrol pwd>
```

Restart and it should create `sensors` for your zones and parameters.
