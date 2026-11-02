# The 8,173 / 19 record

`response.json` is the exact saved provider response. It reports
prompt_eval_count=8173, eval_count=19 and done_reason=length. Its response text
ends after the action colon. `request.json` preserves the exact submitted input,
including num_ctx=8192 and num_predict=384. The offline tokenizer estimate was
10397; 8173 is the serving counter, not that offline estimate. Actual truncated
input token IDs were not retained. Do not present this as an independently
recounted prompt length. `budgeted-result.json` is the matched saved comparison.

The submitted-input estimate exceeds the recorded serving counter by 2,224.
Eleven saved under-cap calibration cases match exactly; the paired budgeted raw
request also matches at 3,303 offline and 3,303 recorded input tokens (53 output).
Together these strongly support serving-side input truncation, not only a reply
running out of room. The omitted input token IDs were not recorded. The configured
8,192 limit is not asserted to be the model's or Spark's maximum.
