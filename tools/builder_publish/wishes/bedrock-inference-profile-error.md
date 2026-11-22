# Name the inference profile to use in the Bedrock on-demand throughput error

I want the error for a model that cannot be invoked on demand to tell me which inference profile to use. Calling the Converse API with the model ID amazon.nova-micro-v1:0 in eu-west-1 returns: "Invocation of model ID amazon.nova-micro-v1:0 with on-demand throughput isn't supported. Retry your request with the ID or ARN of an inference profile that contains this model." The message says a profile is required, but not which one.

The feature would allow me to:
- See the matching inference profile ID or IDs for my Region directly in the error message
- Fix the call without searching the documentation or running list-inference-profiles first

This would help builders calling a newer model for the first time, and anyone writing IAM policies and infrastructure code for it, who currently have to find the profile and its destination Regions separately.
