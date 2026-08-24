# Authenticated human attestation design evaluation

Status: researched design boundary; no signature implementation is enabled.

ProofState's current human attestation is a strict, scoped declaration. It binds
an identity string, issue and expiry times, repository identity, exact commit,
and assertion IDs. Verification must continue to enforce every one of those
checks even if a later schema adds signer authentication. A valid signature is
not permission to widen scope, ignore expiration, accept an unrelated commit, or
replace the attestation payload after verification.

## Decision

Do not add optional signature fields to
`proofstate.dev/attestation/v1alpha1`. Its strict unknown-field behavior is part
of the current fail-closed contract, and embedding algorithm or key-selection
fields would mix payload semantics with trust policy.

If authenticated attestations are implemented later, use a separate signed
envelope schema and a new scorecard schema version. The envelope should carry
the exact existing attestation bytes as its payload, bind a ProofState-specific
payload type, and be verified before those same bytes are parsed as a human
attestation. DSSE is the preferred envelope candidate because its
pre-authentication encoding binds payload type and payload bytes without a JSON
canonicalization dependency. DSSE deliberately leaves algorithms, key
management, trust roots, and verification policy to the application, so an
envelope alone is not a complete design.

Existing `scorecard/v1alpha1` and `attestation/v1alpha1` documents must retain
their current behavior. An authenticated mode must be opt-in through a new
discriminated evidence type in a new scorecard schema, never an interpretation
change to an existing `human_attestation` reference.

## Trust-root profiles

No profile should be selected implicitly, and `keyid` must be treated only as a
lookup hint rather than proof of trust.

| Profile | Required trust input | Identity decision | Offline capability |
| --- | --- | --- | --- |
| Pinned public keys | Repository-owned policy containing algorithm, public-key fingerprint, allowed identity, purpose, and validity bounds | Exact policy mapping from fingerprint to identity and scope | Complete when the policy and key are local |
| Enterprise PKI | Pinned CA roots and intermediates, certificate policy/EKU constraints, identity mapping, and revocation policy | Exact certificate subject or SAN rules defined by local policy | Requires the chain plus sufficiently current, authenticated revocation material |
| Sigstore keyless | Pinned Sigstore trusted root, allowed OIDC issuer and identity, transparency and timestamp policy | Exact issuer and certificate identity constraints | Requires a complete Sigstore bundle, trusted-root snapshot, and verified inclusion/timestamp material |

ProofState should not become a certificate authority, public-key directory, or
OIDC identity provider. Trust policy is local input and must be pinned as
deliberately as the scorecard.

## Revocation and time

Signature validity and attestation validity are separate checks.

- A pinned-key policy needs explicit activation and retirement bounds plus a
  compromise cutoff. Removing a key must affect future evaluations; policy must
  state whether signatures with a trustworthy timestamp before the cutoff
  remain acceptable.
- A PKI profile must validate the chain and certificate validity at the chosen
  signing time, then apply its configured CRL or OCSP policy. Offline mode must
  fail closed when required revocation evidence is absent, stale, or unverifiable.
- A Sigstore profile relies on short-lived certificates, transparency evidence,
  a trusted timestamp or integrated time, and an identity/issuer policy. An OIDC
  account compromise is not automatically erased by certificate expiry, so a
  local identity denylist or compromise cutoff remains necessary.
- The attestation's own `issued_at` and `expires_at` continue to bound the
  declaration. A cryptographic timestamp cannot extend that window.

Every profile needs one documented evaluation instant. Mixing current time,
certificate time, transparency-log time, and payload `issued_at` without an
explicit policy would make offline and online verdicts diverge.

## Offline verification bundle

A portable verification input must contain or pin all material needed for the
selected profile:

1. The signed envelope and its exact payload bytes.
2. The trust-policy schema version and digest.
3. The public key or certificate chain needed by that policy.
4. Any transparency-log inclusion proof, signed checkpoint, or trusted
   timestamp required by the profile.
5. The trusted-root metadata snapshot used to validate those services.
6. Required CRL, OCSP, denylist, or compromise-cutoff material.

The verifier must impose byte, nesting, signature-count, certificate-chain, and
trust-root limits before expensive parsing or cryptography. It must perform no
network access during evaluation. Missing verification material is a stable
fail-closed result, not permission to fall back to an unsigned attestation.

## Schema and implementation gates

An implementation proposal is not ready until it includes:

- a versioned envelope and trust-policy schema with unknown-field rejection;
- cross-language positive and negative test vectors, including payload-type
  confusion, signer substitution, expired scope, revoked keys, stale status,
  missing offline material, duplicate signatures, and threshold failures;
- an explicit algorithm allowlist with no input-controlled negotiation;
- dependency and cryptographic API review;
- deterministic offline verification and stable error codes;
- resource bounds and complexity tests before certificate or signature work;
- an independent security review of the complete trust and time policy.

Until those gates are met, teams needing signer authentication should perform a
separate, policy-controlled signature check before ProofState and retain the
current exact repository, commit, assertion, and expiration validation.

## Primary references

- [DSSE protocol and scope](https://github.com/secure-systems-lab/dsse)
- [in-toto Attestation envelope specification](https://github.com/in-toto/attestation/blob/main/spec/v1/envelope.md)
- [Sigstore security model](https://docs.sigstore.dev/about/security/)
- [Sigstore signature and offline-bundle verification](https://docs.sigstore.dev/cosign/verifying/verify/)
- [RFC 5280 certificate and CRL profile](https://datatracker.ietf.org/doc/html/rfc5280)
