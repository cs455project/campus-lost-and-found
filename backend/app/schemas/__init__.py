# Pydantic schemas: the exact shape of data coming into and going out of
# the API. FastAPI checks incoming data against them automatically and
# answers 422 with a clear message if something is missing or invalid.
# Response schemas also act as a filter: only listed fields are sent back.
