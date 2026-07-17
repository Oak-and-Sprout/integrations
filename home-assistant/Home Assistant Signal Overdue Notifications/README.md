Made this automation for Home Assistant (that already has a Signal messenger integration/addon setup) to notify if feed/diaper is overdue.

You'll need signal setup for Home Assistant already and a `notify:` option in the `configuration.yaml` for the recipients/group you want to send it to.

Replace notify.signal_baby_group in the automation with `notify.[the_name]` name of that notify item (and fill in `YOUR_BABY_ID`).

This can likely be used to notify any Home Assistant notification method since the interface to send notifications in Home Assistant supports various methods in the `notify` section of `configuration.yaml`.
