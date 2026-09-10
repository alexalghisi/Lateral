"""HTTP transport layer.

Routers in this package are deliberately thin. Their responsibility is
translation -- HTTP in, HTTP out -- and nothing else. Business rules live in
`app.services`; persistence lives in `app.repositories`. A router that contains
an `if` statement about domain state is a router that has taken on a job
belonging to a layer below it.
"""
