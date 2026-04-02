# Changelog

<!-- Add new entries under the topmost version heading (currently 0.1.1).
     When you're ready to cut a new release, insert a new ## heading above
     the existing one and start collecting entries there instead. -->

## 0.1.1

- Fixed UK Legislation search returning amending Acts instead of the matched Act when the query resolves to a single result (e.g. "Computer Misuse Act 1990"). The underlying cause was that HTTP redirects from legislation.gov.uk were being followed silently instead of captured.
