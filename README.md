# tap-freshcaller

This is a [Singer](https://singer.io) tap that produces JSON-formatted data
following the [Singer
spec](https://github.com/singer-io/getting-started/blob/master/SPEC.md).

This tap:

- Extracts the following resources:
  - [Users](https://developers.freshcaller.com/api/#list_all_users)
  - [Teams](https://developers.freshcaller.com/api/#list_all_teams)
  - [Calls](https://developers.freshcaller.com/api/#list_all_calls)
  - [Call_Metrics](https://developers.freshcaller.com/api/#list_all_call_metrics)
- Outputs the schema for each resource

---

Copyright &copy; 2022 SageData
