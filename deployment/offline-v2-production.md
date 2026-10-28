# Android 107 offline activation — 28 October 2026

Supersedes the absolute issuance prohibition in production-environment.md.
The existing branch and principal backend are the only deployment target.

Set OFFLINE_FINE_PERMISSIONS_ENABLED=true explicitly to issue V2 grants.
Startup refuses activation unless the existing Ed25519 key parses, the key ID
is nonempty (historical default offline-v1 retained), and
OFFLINE_FINE_AUTHORIZATION_HOURS is between 1 and 168 inclusive.
No private key is generated, replaced, or logged by the application.
ELEVAGE_OFFLINE_READINESS proves an EdDSA encode/decode round trip and emits
only the public-key fingerprint, effective flag, duration, and contract marker.

Capabilities and signed grants expose the authenticated credential_version.
Grant issuance rechecks that epoch after taking the farm lock, so a credential
rotation while an authenticated request waits cannot authorize the old session.
Existing credential rotation revokes grants and online sessions. The signed V2
permission snapshot, current rights, author, farm, primary device, generation,
recorded-time window and durable idempotent receipts remain enforced.

Ordinary operations apply automatically after receipt; stock or permission
conflicts retain the immutable original with NEEDS_RECONCILIATION. This activation
does not authorize reapproval of revoked rights: V2 supervisor decisions remain
READ_ONLY_V2, matching Android 107's supervisor interface. Employee cancellation
continues through its existing separately authorized, tested protocol.

No schema change or data deletion is required. Do not change Android identity,
signing certificate, local database, personal code, or device registration.
Once the owner has prepared the primary tablet and enabled the farm policy, each
operator signs in online with the usual code once to prepare the personal profile.
Seven days is the maximum grant lifetime; renewal requires connectivity.
Automatic retry runs on network restoration while the app is in the foreground,
and on foreground resume. A server receipt is distinct from confirmed application.

M10 acceptance: prepare the personal profile online; disconnect networking; sign
in with the same personal code; consult cached data; create one identifiable
demonstration operation only if desired; restore networking with the app open;
confirm its final receipt and exactly one server effect. Repeat synchronization
and verify the count does not increase. Never share the personal code in reports.
