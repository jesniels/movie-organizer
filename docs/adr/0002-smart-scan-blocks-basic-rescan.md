# Smart Scan blocks basic Rescan

While Smart Scan is running, the Rescan button is disabled. The user must cancel Smart Scan before triggering a Basic Scan.

Smart Scan performs extensive sequential disk and network I/O across all configured locations. Running a parallel Basic Scan against the same paths risks IO contention on network shares, makes progress reporting ambiguous, and could invalidate Smart Scan findings mid-flight (e.g. items disappear because the cache is cleared and rewritten). The alternative — allowing both to run concurrently with separate, independently-managed caches — was rejected because the increased implementation complexity was not justified given that Smart Scan already surfaces everything a Basic Scan does, and more.

## Consequences

If a user finishes an Organize operation while Smart Scan is running, the post-organize "Rescan now?" prompt shows a warning and a "Stop Smart Scan" button inline so the user can unblock the rescan without navigating away.
