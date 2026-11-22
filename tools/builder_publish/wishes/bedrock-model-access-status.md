# Show whether my account can invoke a Bedrock model before I call it

I want a way to check, before I call a model, whether my account is allowed to invoke it. The list-inference-profiles API shows the EU Claude Sonnet 4.6 profile with the status ACTIVE, but invoking it returns a ResourceNotFoundException: "Model use case details have not been submitted for this account. Fill out the Anthropic use case details form before using the model. If you have already filled out the form, try again in 15 minutes."

The feature would allow me to:
- See in the API and CLI output whether a required use case form or other access step is still pending for a model
- Get an access-related error type when access is missing, instead of a resource-not-found error
- Know how long to wait after submitting the form

This would help builders who automate Bedrock deployments, because an ACTIVE profile that cannot be called causes failed deployments that are hard to diagnose.
