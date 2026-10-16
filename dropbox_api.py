assume_role is called once per certificate. A sweep repeats STS calls for no reason. Build the Route 53 client once per invocation, or cache it.
