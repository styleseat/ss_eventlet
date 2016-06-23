# ss\_eventlet: Eventlet library extensions

This library extends [eventlet] (https://github.com/eventlet/eventlet/),
providing additional patched modules and utilities.

# Sample Usage

```python
import ss_eventlet
from ss_eventlet.eventlet_modules import requests

# Retrieve url, throwing Timeout, an exception inheriting from Exception,
# if the request takes longer than 2s.
with ss_eventlet.Timeout(2.0):
  response = requests.get(url)
```
