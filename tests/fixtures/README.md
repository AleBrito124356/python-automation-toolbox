# Test fixtures

Hand-written JSON bodies shaped like the public, key-less APIs the scripts
use, so the suite never needs the network:

- `open_meteo_geocoding.json` - `GET https://geocoding-api.open-meteo.com/v1/search`
- `open_meteo_forecast.json` - `GET https://api.open-meteo.com/v1/forecast` with the
  `current=` and `daily=` fields that `18_weather_cli.py` requests
- `frankfurter_latest_USD.json` - `GET https://api.frankfurter.app/latest?from=USD`
- `frankfurter_currencies.json` - `GET https://api.frankfurter.app/currencies`

The numbers are illustrative, not real market or weather data.
