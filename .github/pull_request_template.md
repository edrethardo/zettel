**What this changes, and why**

<!-- The why matters more than the what. If you are changing a decision that
     an existing comment explains, say why that reasoning no longer holds. -->

**Numbers, if you are claiming an improvement**

<!-- "Faster" is not a claim. "20 s -> 6 s per turn, measured over 128 dishes"
     is. Say how you measured. -->

**Checklist**

- [ ] `.venv/bin/python -m pytest -q` is green
- [ ] No test talks to the network (shop, recipe site, model)
- [ ] New behaviour has a test; anything that could break silently has one too
- [ ] No real household data, screenshots included
- [ ] If this touches a crawler: `robots.txt` allows it, requests are serial
      and at least 1.5 s apart, and the User-Agent says who we are
- [ ] If this adds screen text: keys in `de.json` **and** a translation where
      you can

Comments and identifiers in this repo are German; your PR description does not
have to be. See `CONTRIBUTING.md`.
