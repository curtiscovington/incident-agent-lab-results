# Submitted input and serving counters

The native figure distinguishes the 10,397-token full-input estimate from the
8,173-token serving counter and 19-token output. All bars share a 0–10,397 scale.
The 2,224-token difference supports an inference of serving-side input truncation;
which tokens were omitted is unknown. The budgeted pair is 3,303 input/53 output.
See input-token-validation.json and the exact saved request/response objects.
The 8,192-token context was configured for this study; no hardware/model maximum
or reason for choosing that original limit is inferred.
