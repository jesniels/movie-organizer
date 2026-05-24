# Cancel stops cleanly with no rollback; partial destination files are deleted

Cancelling an in-progress Organize operation stops at the next item boundary and does not attempt to undo already-completed moves. If a file transfer is interrupted mid-copy, the partial destination file is deleted. Everything else already moved stays at its new location.

Full rollback was considered and rejected for three reasons: rolling back a partial move across a network share is risky because the original path may already be gone or the network may have dropped; a failed rollback leaves data in a worse state than a clean stop; and the post-operation "Rescan now?" prompt makes any partial result immediately visible to the user so nothing is silently lost.

Deleting partial destination files is the one cleanup exception: a half-written file at the destination is unambiguously useless and would confuse both the user and Jellyfin, so removing it is safe regardless of network state.
