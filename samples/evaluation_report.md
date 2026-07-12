# Evaluation report

Retrieval and answer quality of the NIST SP 800-53 policy pipeline over the hand-labeled golden set (`evaluation/golden_set.json`). Recall and graded NDCG@5 are computed directly from the golden qrels — pure, deterministic math with no external evaluator. Faithfulness and answer relevancy are LLM-judge scores (integer 1-5) from two PydanticAI judge agents on the same Azure OpenAI deployment the pipeline uses.

Recall is shown two ways. **Exact-ID** credits only a retrieved control whose ID matches a labelled one. **Base-family** also credits a retrieved enhancement whose base control was labelled — `ia-2.6` counts toward the `ia-2` need — because a NIST SP 800-53 enhancement is a more specific form of its base control, so retrieving it genuinely answers the need. The exact-ID column is a strict lower bound; the base-family column is the fairer measure for this hierarchical catalog. NDCG@5 stays exact-ID, truncated at the pipeline's own top-k; it is computed in-house and is **not comparable** to the NDCG@3 the Azure AI `DocumentRetrievalEvaluator` reported in earlier committed runs.

## Aggregate

On-topic queries: 13 · Fallback queries: 2/2 returned the safe fallback · Invented citations: 0

| Metric | Exact-ID | Base-family |
|---|---|---|
| Recall | 0.455 | 0.619 |
| NDCG@5 | 0.494 | — |
| Faithfulness (1-5) | 5.00 | — |
| Answer relevancy (1-5) | 5.00 | — |

## Per-query results

### Q1: What controls apply to API security?

- **Plan:** API security; application programming interface
- **Retrieved (6):** si-3.8 (2.40), sa-11.9 (2.24), ac-16.7 (2.22), ia-9 (2.14), ac-4.22 (2.13), ac-3.12 (2.12)
- **Relevant (qrels):** sc-7(2), sc-8(2), ac-4(2), ac-3(1), ia-2(1)
- **Recall:** 0.000 exact-ID / 0.400 base-family · **NDCG@5:** 0.000
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ia-9, ac-16.7, ac-3.12, ac-4.22, sa-11.9, si-3.8]; invented [none]
- **Answer:** The supplied controls that apply to API security address authentication of services, application-level access assertions and enforcement, consistent attribute interpretation across components, use of interactive testing, and detection of unauthorized commands. Specifically:

- Require services and applications that implement or consume APIs to be uniquely identified and authenticated before they communicate, which helps ensure only authorized API clients and services can establish connections: [ia-9].

- Ensure distributed API components interpret security and privacy attributes consistently when those attributes are transmitted between components (for example, tokens, claims, or attribute flags), reducing mismatches that could lead to privilege or data exposure: [ac-16.7].

- Require applications to assert what access they need during installation and provide enforcement to prevent unauthorized access (and require approval for later access changes). Applied to APIs, this supports least-privilege declarations for API consumers/clients and runtime enforcement of those declared permissions: [ac-3.12].

- Provide mechanisms to allow a single device to access resources across multiple security domains while preventing information flow between domains. For APIs that mediate cross-domain access, this supports containment and information flow controls between domains: [ac-4.22].

- Require developers to use interactive application security testing tools to identify and document security flaws in the application or service (including API logic and endpoints): [sa-11.9].

- Detect unauthorized operating system commands invoked via kernel APIs on specified system hardware components and choose to warn, audit, or prevent such commands. This helps protect API hosts from unauthorized command execution as part of API attacks: [si-3.8].

If you need controls for other API-specific topics (for example, rate limiting, payload validation, transport-layer protection, token formats, logging schemas, or API gateway configuration), those are not covered in the supplied controls; the supplied items do not address those specific mechanisms.

### Q2: How should sensitive data be protected in cloud systems?

- **Plan:** Protecting sensitive data in cloud systems (encryption, data-at-rest, data-in-transit, key management); Access control for sensitive data and privileged user access; Data retention, disposal, and sanitization of sensitive data
- **Retrieved (14):** ac-2.7 (2.79), ac-3.4 (2.79), ac-17.4 (2.76), ac-17.2 (2.75), ac-6 (2.70), mp-6 (2.65), ac-4.25 (2.55), si-12.3 (2.43), sc-8.1 (2.33), sc-28 (2.32), si-12 (2.29), ia-13.1 (2.28), sc-28.1 (2.28), mp-4 (2.22)
- **Relevant (qrels):** sc-28(2), sc-8(2), sc-13(2), sc-12(1)
- **Recall:** 0.250 exact-ID / 0.500 base-family · **NDCG@5:** 0.000
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ac-6, ac-2.7, ac-17.4, ac-17.2, sc-8.1, sc-28.1, sc-28, ac-3.4, ia-13.1, mp-4, mp-6, ac-4.25, si-12.3, si-12]; invented [none]
- **Answer:** To protect sensitive data in cloud systems, apply the supplied controls as follows:

- Limit who can access sensitive data by enforcing least privilege so users and processes have only the authorizations necessary for their tasks [ac-6]. Establish and administer privileged accounts under an access scheme (e.g., role- or attribute-based), monitor privileged assignments and changes, and revoke privileged access when no longer appropriate [ac-2.7]. Authorize and document any remote privileged command execution and access to security-relevant information, restricting such remote use to organization-defined needs and recording the rationale in the system security plan [ac-17.4].

- Protect data in transit and remote sessions with cryptography: implement cryptographic mechanisms to protect confidentiality and integrity of remote access sessions [ac-17.2] and to prevent unauthorized disclosure and detect changes during transmission [sc-8.1].

- Protect data at rest with cryptography: implement cryptographic mechanisms to prevent unauthorized disclosure and modification of specified information at rest on system components or media [sc-28.1], and more generally protect the confidentiality and/or integrity of information at rest as selected in policy [sc-28].

- Control discretionary flow and sharing: enforce the organization-defined discretionary access control policy to govern whether subjects can pass information, grant privileges, change security attributes, set attributes on new objects, or alter access rules [ac-3.4].

- Secure and manage cryptographic keys for access tokens so keys are generated, managed, and protected from disclosure and misuse [ia-13.1].

- Protect physical and logical media: physically control and securely store organization-defined media within controlled areas and maintain protection until media are destroyed or sanitized using approved techniques [mp-4]. Sanitize system media prior to disposal, release, or reuse using organization-defined sanitization techniques commensurate with the information’s security category [mp-6]. When transferring information between different security domains, sanitize data to minimize delivery or control of malicious content, spillage of sensitive information, etc., per policy [ac-4.25].

- Dispose of retained information using organization-defined disposal, destruction, or erasure techniques after the retention period and manage/retain information per applicable requirements [si-12.3] [si-12].

These controls together cover access restriction and monitoring ([ac-6], [ac-2.7], [ac-3.4], [ac-17.4]), cryptographic protection in transit and at rest ([ac-17.2], [sc-8.1], [sc-28.1], [sc-28], [ia-13.1]), media handling and sanitization ([mp-4], [mp-6], [ac-4.25]), and information disposal/retention ([si-12.3], [si-12]). If you need operational specifics (e.g., exact algorithms, key lengths, or concrete sanitization procedures), those are not provided in the supplied controls.

### Q3: Summarise requirements for access control

- **Plan:** Access control requirements
- **Retrieved (5):** ac-3.12 (2.97), ac-3.3 (2.87), ac-19 (2.84), ac-6.2 (2.84), ac-20.1 (2.78)
- **Relevant (qrels):** ac-2(2), ac-3(2), ac-6(2), ac-1(1), ac-17(1)
- **Recall:** 0.000 exact-ID / 0.400 base-family · **NDCG@5:** 0.000
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ac-3.12, ac-3.3, ac-19, ac-6.2, ac-20.1]; invented [none]
- **Answer:** Here are the summarized access-control requirements drawn from the supplied controls:

- Applications must declare during installation what access they require to specified system applications and functions; the system must provide mechanisms that enforce those assertions to prevent unauthorized access, and any post-installation changes to application access must be approved [ac-3.12].

- The organization must enforce a defined mandatory access control (MAC) policy across the covered subjects and objects. That policy must be uniformly applied and constrain subjects who are granted access so they cannot: pass information to unauthorized subjects/objects, delegate their privileges, change specified security attributes, choose security attributes for newly created/modified objects, or modify access-control rules. The policy may also allow specified subjects to be explicitly granted privileges that exempt them from some or all of those constraints [ac-3.3].

- For mobile devices under organizational control, the organization must establish configuration, connection, and implementation requirements (including for devices outside controlled areas) and must authorize mobile-device connections to organizational systems [ac-19].

- Users who have system accounts or roles that provide access to security functions or security-relevant information must use non-privileged accounts/roles when performing nonsecurity functions (i.e., separate privileged security use from routine/nonsecurity use) [ac-6.2].

- External systems may be used to access organizational systems or to process/store/transmit organization-controlled information only after either verifying that the external system implements required controls per the organization’s security/privacy policies and plans, or after retaining approved system-connection or processing agreements with the external hosting entity [ac-20.1].

If you need controls mapped to specific procedures, roles, or a particular system, those assignment placeholders (e.g., which applications/functions, which subjects/privileges) must be defined by the organization; the supplied controls require enforcement and approval but do not specify those concrete assignments [ac-3.12], [ac-3.3].

### Q4: What policies relate to logging and monitoring?

- **Plan:** Audit and accountability logging and monitoring; Continuous monitoring and security monitoring
- **Retrieved (10):** au-2 (2.75), pm-31 (2.73), ca-7 (2.63), au-16 (2.52), au-6.5 (2.51), au-14 (2.51), ma-4.1 (2.50), ca-7.4 (2.41), pm-14 (2.39), ca-5 (2.31)
- **Relevant (qrels):** au-2(2), au-6(2), au-12(2), si-4(2), au-3(1), ca-7(1)
- **Recall:** 0.333 exact-ID / 0.500 base-family · **NDCG@5:** 0.454
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [au-2, pm-31, ca-7, au-16, au-6.5, au-14, ma-4.1, ca-7.4, pm-14, ca-5]; invented [none]
- **Answer:** The supplied controls indicate the following policies and policy-related activities that specifically relate to logging and monitoring:

- Define what events the system must log, how often, and why those events are sufficient for investigations (event logging policy elements) — [au-2] (a, c, d, e).  
- Coordinate event logging requirements with other organizational entities that need audit information — [au-2] (b).  
- Define organization-wide continuous monitoring strategy and metrics, including monitoring and assessment frequencies, ongoing monitoring, correlation/analysis of monitoring and assessment data, response actions, and reporting of security/privacy status — [pm-31] (a–f).  
- Define a system-level continuous monitoring strategy aligned to the organization strategy, including system-level metrics, monitoring and assessment frequencies, ongoing assessments and monitoring, correlation/analysis, response actions, and system-level reporting — [ca-7] (a–g).  
- Specify methods and requirements for coordinating audit information transmitted across organizational boundaries (cross-organizational audit logging policy) — [au-16].  
- Require integration of audit-record analysis with other data sources (e.g., vulnerability scans, performance, system monitoring, or other assigned sources) to better identify unusual activity — [au-6.5].  
- Provide session-audit policy elements for when and how user sessions may be recorded or audited, including legal review and compliance with laws and policies — [au-14] (a, b).  
- Require logging and review of nonlocal maintenance and diagnostic session events and review of those audit records to detect anomalies — [ma-4.1] (a, b).  
- Include risk monitoring (effectiveness, compliance, change) as an explicit part of the continuous monitoring strategy — [ca-7.4] (a–c).  
- Ensure testing, training, and monitoring plans are developed, maintained, executed, and reviewed for consistency with risk management and priorities — [pm-14] (a, b).  
- Tie continuous monitoring findings into the plan of action and milestones process so remediation actions and POA&Ms are developed and updated based on monitoring and assessment results — [ca-5] (a, b).

If you need the specific organization-defined values (e.g., which event types, which metrics, monitoring/assessment frequencies, reporting recipients, coordination methods), those are assignments in the controls and are not specified in the supplied text. The controls above describe what policies must cover but do not themselves supply the concrete values.

### Q5: What are the requirements for multi-factor authentication?

- **Plan:** multi-factor authentication
- **Retrieved (5):** ia-2.6 (2.68), ia-2.1 (2.64), ia-2.2 (2.62), ac-7.4 (2.46), ia-8.1 (2.29)
- **Relevant (qrels):** ia-2(2), ia-2.1(2), ia-2.2(2), ia-5(1)
- **Recall:** 0.500 exact-ID / 0.500 base-family · **NDCG@5:** 0.482
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ia-2.1, ia-2.2, ia-2.6, ac-7.4, ia-8.1]; invented [none]
- **Answer:** The supplied controls require that multi-factor authentication (MFA) be implemented for both privileged and non‑privileged accounts and may have additional device and alternative‑factor requirements:

- MFA must be implemented for privileged accounts [ia-2.1] and for non‑privileged accounts [ia-2.2].

- Where specified for local, network, or remote access to privileged or non‑privileged accounts, MFA must use at least one factor provided by a device separate from the system being accessed, and that device must meet the organization‑assigned “strength of mechanism” requirements [ia-2.6].

- The organization may allow alternative authentication factors after a configurable number of consecutive invalid logon attempts; such use must be limited to an organization‑defined number of consecutive invalid attempts within an organization‑defined time period [ac-7.4].

- If applicable, Personal Identity Verification (PIV) credentials from other federal agencies must be accepted and electronically verified [ia-8.1].

If you need specifics not provided here (for example, which access types—local, network, remote—are in scope; the exact “strength of mechanism” required for the separate device; or the numeric limits and time periods for alternative-factor use), those details are not included in the supplied controls and must be provided by the organization to fully define the MFA requirements.

### Q6: How should cryptographic keys be established and managed?

- **Plan:** cryptographic key establishment and management; cryptographic key lifecycle
- **Retrieved (8):** sc-12 (3.16), sc-12.3 (2.61), sc-12.2 (2.56), ia-5.14 (2.54), sc-28.3 (2.54), sa-9.6 (2.16), sc-12.1 (2.13), sc-12.6 (2.11)
- **Relevant (qrels):** sc-12(2), sc-13(1), sc-17(1)
- **Recall:** 0.333 exact-ID / 0.333 base-family · **NDCG@5:** 0.639
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [sc-12, sc-12.1, sc-12.2, sc-12.3, sc-28.3, sa-9.6, sc-12.6, ia-5.14]; invented [none]
- **Answer:** Use the supplied controls to define key establishment and management as follows.

Key lifecycle and requirements
- Establish and manage cryptographic keys whenever cryptography is used in the system, in accordance with organization-defined key management requirements [sc-12].  
- Ensure availability of information if users lose keys (for example, key recovery or escrow) to maintain access to encrypted information [sc-12.1].

Key types and production/distribution methods
- For symmetric keys, produce, control, and distribute them using selected validated/approved key management technology and processes (for example, NIST FIPS-validated or NSA-approved per the organization’s selection) [sc-12.2].  
- For asymmetric (public/private) keys, produce, control, and distribute them using the organization’s selected approach (for example NSA-approved key management, prepositioned keying material, or approved/issued PKI certificates and hardware tokens as indicated by organizational selection) [sc-12.3].

Storage and protection of keys
- Provide protected storage for cryptographic keys, using safeguards as assigned by the organization and/or a hardware-protected key store as selected [sc-28.3].

Organizational control and third-party use
- Maintain exclusive organizational control of cryptographic keys for material that is stored or transmitted through external systems (i.e., do not cede key control to the external service) [sa-9.6].  
- When external service providers store encrypted information on behalf of the organization, ensure the organization maintains physical control of the cryptographic keys used to protect that information [sc-12.6].

PKI trust stores
- For PKI-based authentication, employ an organization-wide methodology to manage the contents of PKI trust stores across all platforms (networks, OS, browsers, and applications) so trust anchors and revocations are consistently controlled [ia-5.14].

What these controls do not specify
- The supplied controls do not provide the organization-defined specifics named in several selections (for example, the exact key lifetimes, rotation/retirement intervals, specific key recovery/escrow procedures, detailed procedural steps for key generation, exact safeguards for protected storage, or the precise selections among the offered options). Where a control references “[Assignment:]” or “[Selection:]”, the organization must define those concrete requirements and choices [sc-12], [sc-12.2], [sc-12.3], [sc-28.3].

### Q7: What controls govern incident response and reporting?

- **Plan:** Incident response policy and planning; Incident handling and reporting requirements
- **Retrieved (7):** ir-8 (2.99), ir-4 (2.80), ir-1 (2.78), ir-6 (2.70), sa-15.10 (2.68), ir-8.1 (2.66), ir-7 (2.44)
- **Relevant (qrels):** ir-4(2), ir-6(2), ir-8(2), ir-5(1)
- **Recall:** 0.750 exact-ID / 0.750 base-family · **NDCG@5:** 0.879
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ir-1, ir-8, ir-4, ir-6, ir-7, ir-8.1, sa-15.10]; invented [none]
- **Answer:** The controls governing incident response and reporting in the supplied set are:

- Policy and procedures establishment for incident response: develop, document, disseminate, designate an official to manage them, and review/update regularly [ir-1]. Use this to set scope, roles, responsibilities, management commitment, coordination, compliance, and procedures to implement incident response controls [ir-1].

- Incident response plan content and management: require an incident response plan that provides a roadmap, describes structure/organization, shows how the capability fits into the organization, meets organization-unique requirements, defines reportable incidents, provides metrics, identifies needed resources and management support, addresses incident information sharing, is reviewed/approved, and designates responsibility; distribute the plan to response personnel, update it for system/organizational changes or lessons learned, communicate changes to designated personnel, and protect the plan from unauthorized disclosure/modification [ir-8].

- Incident handling operations: implement an incident handling capability consistent with the incident response plan covering preparation, detection and analysis, containment, eradication, and recovery; coordinate with contingency planning; incorporate lessons learned into procedures, training, and testing; and ensure handling activities are comparable and predictable across the organization [ir-4].

- Reporting requirements and authorities: require personnel to report suspected incidents to the organizational incident response capability within an organization-defined time period, and report incident information to organization-defined authorities [ir-6].

- Incident response assistance resource: provide an incident response support resource that offers advice and assistance to system users for handling and reporting incidents [ir-7].

- Breach-specific requirements for PII: for breaches involving personally identifiable information, require the incident response plan to include processes for determining notice to individuals/organizations, assessing harm and mitigation, and identifying applicable privacy requirements [ir-8.1].

- Acquisition requirement: require system developers/providers to provide, implement, and test an incident response plan for the system or service they deliver [sa-15.10].

If you need specifics such as the exact reporting time period, the identities of authorities to notify, or the review frequencies, those are not specified in the supplied controls and must be defined by the organization (the controls indicate these are organization-defined assignments) [ir-6], [ir-8], [ir-1].

### Q8: What are the requirements for security awareness and role-based training?

- **Plan:** AT-2 Security Awareness Training; AT-3 Role-Based Security Training
- **Retrieved (9):** at-3 (2.41), sa-16 (2.28), at-4 (2.26), at-3.2 (2.19), at-2.4 (2.18), at-3.3 (2.18), sr-11.1 (2.18), at-2 (2.14), pe-2.1 (2.03)
- **Relevant (qrels):** at-2(2), at-3(2), at-4(1)
- **Recall:** 1.000 exact-ID / 1.000 base-family · **NDCG@5:** 0.665
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [sa-16, at-3.2, at-2.4, at-3.3, sr-11.1, pe-2.1]; invented [none]
- **Answer:** Requirements for security awareness and role‑based training based on the supplied controls:

- Provide role‑based training to personnel in organization‑defined roles and responsibilities. This training must be delivered before those personnel are authorized to access the system, information, or perform assigned duties, and again at an organization‑defined recurring frequency; it must also be provided when required by system changes [at-3 a.1–a.2].

- Maintain a process to update role‑based training content at an organization‑defined frequency and following organization‑defined events, and incorporate lessons learned from internal or external security incidents or breaches into role‑based training [at-3 b–c].

- Ensure developers of systems, components, or services provide training on correct use and operation of implemented security and privacy functions, controls, and mechanisms (the specific training is organization‑defined) [sa-16].

- Document and monitor all information security and privacy training activities, including awareness and role‑based training, and retain individual training records for an organization‑defined time period [at-4 a–b].

- Provide initial and recurring (organization‑defined frequency) training in the employment and operation of physical security controls to designated personnel or roles [at-3.2].

- Include literacy training for users (managers, senior executives, contractors) as part of initial onboarding and at an organization‑defined recurring frequency thereafter, and when required by system changes or specified events; employ organization‑defined awareness techniques, update literacy/awareness content at an organization‑defined frequency and after specified events, and incorporate lessons learned from incidents into literacy training [at-2 a–d].

- Provide literacy training focused on recognizing suspicious communications and anomalous system behavior using organization‑defined indicators of malicious code [at-2.4].

- Include practical exercises in security and privacy training to reinforce objectives [at-3.3].

- Train supply‑chain or other designated personnel/roles to detect counterfeit system components (hardware, software, firmware) where applicable [sr-11.1].

- For physical access, authorize facility access based on position or role (this supports role‑based physical access training and authorization) [pe-2.1].

If you need the specific organization‑defined frequencies, events, roles, record retention periods, awareness techniques, or developer training content, those values are not defined in the supplied controls and must be provided by your organization to fully implement these requirements.

### Q9: How should removable media and portable storage devices be controlled?

- **Plan:** Removable media and portable storage device controls
- **Retrieved (5):** mp-7 (2.27), mp-4 (2.23), ac-20.2 (2.20), mp-6.3 (2.12), mp-4.2 (2.09)
- **Relevant (qrels):** mp-5(2), mp-7(2), mp-2(1), mp-4(1)
- **Recall:** 0.500 exact-ID / 0.500 base-family · **NDCG@5:** 0.628
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [mp-4.2, ac-20.2, mp-6.3]; invented [none]
- **Answer:** Control of removable media and portable storage devices should be accomplished using the organization’s media protection and access-control measures described by the supplied controls.

- Restrict or prohibit types of media on specific systems. Apply an organizational policy that either restricts or prohibits use of particular system media on defined systems or components and implement the specified controls for that restriction/prohibition [mp-7a]. Also prohibit portable storage devices that do not have an identifiable owner from being used on organizational systems [mp-7b].

- Physically control and securely store defined types of digital and non-digital media in organization-defined controlled areas, and protect those media until they are destroyed or sanitized using approved equipment, techniques, and procedures [mp-4a, mp-4b]. Ensure storage-area access is restricted and that access attempts and grants are logged using organization-defined automated mechanisms [mp-4.2].

- Limit use of organization-controlled portable storage devices on external systems by authorized individuals and enforce the organization’s specified restrictions on such use [ac-20.2].

- Before connecting portable storage devices to systems, apply nondestructive sanitization techniques under the circumstances the organization defines (for example, when devices originate from external sources or cannot be verified) so that devices are sanitized as required prior to connection [mp-6.3].

If you need operational details (for example, specific sanitization methods, exact types of media to restrict or prohibit, or the particular automated mechanisms to use for logging access), those are not provided in the supplied controls; the controls only specify that such selections, restrictions, locations, mechanisms, and circumstances be defined and implemented by the organization [mp-7a, mp-4a, mp-4b, mp-4.2, ac-20.2, mp-6.3].

### Q10: What controls address vulnerability scanning and flaw remediation?

- **Plan:** vulnerability scanning; flaw remediation
- **Retrieved (10):** ra-5 (3.12), si-2 (3.05), si-2.3 (2.71), au-6.5 (2.64), ca-2.2 (2.57), sa-11 (2.56), ra-5.2 (2.51), ra-5.10 (2.50), si-2.4 (2.45), si-3.10 (2.42)
- **Relevant (qrels):** ra-5(2), si-2(2), si-3(1)
- **Recall:** 0.667 exact-ID / 1.000 base-family · **NDCG@5:** 0.867
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ra-5, ra-5.2, ra-5.10, au-6.5, si-2, si-2.3, si-2.4, sa-11, si-3.10]; invented [none]
- **Answer:** The supplied controls that address vulnerability scanning and flaw remediation are:

- Vulnerability scanning and monitoring: [ra-5] (Vulnerability Monitoring and Scanning) requires monitoring and scanning for vulnerabilities on a defined frequency or when new vulnerabilities are reported, using interoperable tools and standards for enumerating platforms/flaws/configurations, formatting checklists/test procedures, and measuring impact; analyzing scan reports; updating scanned vulnerabilities capability; and sharing results [ra-5] and the related update requirement [ra-5.2] (Update Vulnerabilities to Be Scanned) which specifies when vulnerabilities to be scanned are updated.

- Correlation and analysis of scan output: [ra-5.10] (Correlate Scanning Information) requires correlating vulnerability scan outputs to detect multi-vulnerability and multi-hop attack vectors; and [au-6.5] (Integrated Analysis of Audit Records) supports integrating vulnerability scanning information with audit/monitoring data for enhanced detection.

- Flaw remediation (identification through implementation and timelines): [si-2] (Flaw Remediation) requires identifying, reporting, correcting system flaws; testing updates for effectiveness and side effects before installation; installing security-relevant updates within an organizational time period; and incorporating flaw remediation into configuration management. Timing metrics and benchmarks for remediation are specified by [si-2.3] (Time to Remediate Flaws and Benchmarks for Corrective Actions), which requires measuring time from identification to remediation and establishing benchmarks.

- Automation and tools to assist remediation: [si-2.4] (Automated Patch Management Tools) requires employing automated patch management tools to facilitate remediation for specified components.

- Developer responsibilities and integrating remediation into lifecycle testing: [sa-11] (Developer Testing and Evaluation) requires developers to implement ongoing security testing, provide evidence, implement a verifiable flaw remediation process, and correct flaws identified during testing.

- Incorporation into incident response when malicious code is involved: [si-3.10] (Malicious Code Analysis) requires using analysis tools/techniques for malicious code and incorporating those results into incident response and flaw remediation processes.

If you need a concise mapping (scan vs. remediate): use [ra-5], [ra-5.2], [ra-5.10], [au-6.5] for scanning/analysis; use [si-2], [si-2.3], [si-2.4], [sa-11], and [si-3.10] for remediation and integration into processes.

### Q11: What are the contingency planning and system backup requirements?

- **Plan:** Contingency planning; System and information backup
- **Retrieved (10):** cp-2 (3.43), cp-8.4 (2.99), cp-3.1 (2.98), cp-1 (2.98), cp-4 (2.92), cp-9 (2.74), cp-6 (2.56), cp-9.6 (2.55), cp-9.3 (2.47), sa-15.11 (2.38)
- **Relevant (qrels):** cp-2(2), cp-9(2), cp-10(1)
- **Recall:** 0.667 exact-ID / 0.667 base-family · **NDCG@5:** 0.532
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [cp-1, cp-3.1, cp-9.3, cp-9.6, sa-15.11, cp-2, cp-4, cp-8.4, cp-9]; invented [none]
- **Answer:** Contingency planning requirements

- Establish and document a contingency planning policy and procedures, assign an official to manage them, and review/update the policy and procedures on an organization-defined schedule and after specified events [cp-1].  
- Develop a system contingency plan that:
  - Identifies essential mission/business functions and contingency requirements, recovery objectives, restoration priorities, and metrics; assigns roles, responsibilities, and contact information; and addresses maintaining essential functions during disruption and eventual full system restoration without control degradation [cp-2(a)(1)-(6)].  
  - Includes sharing of contingency information, is reviewed and approved by organization-defined personnel/roles, and is protected from unauthorized disclosure and modification [cp-2(a)(6)-(7), cp-2(h)].  
- Distribute copies of the contingency plan to organization-defined key contingency personnel and organizational elements, coordinate contingency planning with incident handling, and review and update the plan at an organization-defined frequency and when changes occur; communicate plan changes to key personnel and incorporate lessons learned from testing/training/actual incidents [cp-2(b)-(g)].

Contingency training and testing

- Include simulated events within contingency training to improve personnel response in crises [cp-3.1].  
- Test the contingency plan at an organization-defined frequency using organization-defined test types, review test results, and initiate corrective actions as needed [cp-4(a)-(c)].

Provider contingency

- Require primary and alternate telecommunications providers to have contingency plans, review those plans for conformance to organizational contingency requirements, and obtain evidence of providers’ contingency testing and training at an organization-defined frequency [cp-8.4(a)-(c)].

System backup and storage requirements

- Back up user-level information, system-level information, and system documentation (including security/privacy documentation) at organization-defined frequencies, and protect confidentiality, integrity, and availability of backup information [cp-9(a)-(d)].  
- Maintain backups in an alternate storage site (with necessary agreements) that provides controls equivalent to the primary site [cp-6(a)-(b)].  
- For critical system software and other security-related information, store backup copies in a separate facility or a fire-rated container not co-located with the operational system [cp-9.3].  
- Alternatively or additionally, maintain a redundant secondary system that is geographically separate (not collocated) and can be activated without data loss or operational disruption [cp-9.6].  
- If procuring system components, require developers to archive the delivered system/component along with evidence supporting final security and privacy review [sa-15.11].

If you need organization-specific frequencies, designated personnel, or the list of organization-defined tests and assignments, those details are not specified in the supplied controls and must be defined by your organization (the controls require insertion of those organization-defined values) [cp-1], [cp-2], [cp-4], [cp-8.4], [cp-9].

### Q12: How is least privilege enforced and separation of duties maintained?

- **Plan:** least privilege; separation of duties
- **Retrieved (10):** ac-6 (3.15), sa-17.7 (2.98), ac-5 (2.61), cm-7.6 (2.46), sa-8.14 (2.41), ac-6.2 (2.23), sc-22 (1.95), sc-7.20 (1.93), au-16.3 (1.89), si-13.1 (1.85)
- **Relevant (qrels):** ac-6(2), ac-5(2), ac-6.1(1)
- **Recall:** 0.667 exact-ID / 1.000 base-family · **NDCG@5:** 0.797
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ac-6, sa-17.7, sa-8.14, ac-6.2, cm-7.6, ac-5, sc-7.20, sc-22, au-16.3, si-13.1]; invented [none]
- **Answer:** Least privilege is enforced by applying the principle that users and processes are granted only the access necessary to accomplish assigned tasks [ac-6]. Procurement and system design reinforce that enforcement by requiring developers to structure security-relevant hardware, software, and firmware so access can be controlled to support least privilege [sa-17.7] and by explicitly implementing least-privilege design in designated systems or components [sa-8.14]. Operationally, accounts that have access to security functions or security-relevant information are constrained so that when those account holders perform nonsecurity functions they use non‑privileged accounts or roles [ac-6.2]. For user-installed software, execution is restricted to confined physical or virtual environments with limited privileges, which helps ensure such software cannot escalate privileges beyond its minimal needs [cm-7.6].

Separation of duties is maintained by first identifying and documenting the duties of individuals and then defining system access authorizations to support those separations—i.e., mapping documented duties to access controls that prevent conflicting functions from being held by the same individual [ac-5]. Technical controls that support separation include dynamic isolation/segregation capabilities to isolate system components from others as needed [sc-7.20], and role separation in critical infrastructure such as name/address resolution services to separate internal and external roles [sc-22].

Notes on related operational behaviors: audit information is protected from being trivially associated with individuals when transmitted across boundaries by implementing disassociability measures for audit data [au-16.3]. Finally, to avoid unexpected consolidation of responsibilities when components fail, component responsibilities are transferred to substitute components within an organizationally defined fraction of mean time to failure [si-13.1].

If you need specific mappings of duties to access authorizations or concrete examples of confined environments, those requirements (e.g., the particular duties, the list of user‑installed software to confine, or the exact substitution fraction for failures) are not provided in the supplied controls and would need to be defined to create detailed policies.

### Q13: What controls apply to configuration management and baseline configurations?

- **Plan:** Configuration management and baseline configurations
- **Retrieved (5):** cm-2 (3.12), cm-2.6 (2.84), cm-3.3 (2.64), cm-2.2 (2.62), cm-9 (2.40)
- **Relevant (qrels):** cm-2(2), cm-6(2), cm-3(1), cm-7(1)
- **Recall:** 0.250 exact-ID / 0.500 base-family · **NDCG@5:** 0.477
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [cm-2, cm-2.6, cm-2.2, cm-3.3, cm-9]; invented [none]
- **Answer:** Controls that apply to configuration management and baseline configurations from the supplied set are:

- [cm-2] Baseline Configuration requires you to develop, document, and maintain a current baseline configuration under configuration control, and to review and update that baseline on a defined frequency, when specified circumstances occur, and whenever system components are installed or upgraded [cm-2].

- [cm-2.6] Development and Test Environments requires maintaining a baseline configuration for development and test environments that is managed separately from the operational baseline configuration, i.e., separate baselines for dev/test vs. production [cm-2.6].

- [cm-2.2] Automation Support for Accuracy and Currency requires using assigned automated mechanisms to maintain the currency, completeness, accuracy, and availability of the system baseline configuration [cm-2.2].

- [cm-3.3] Automated Change Implementation requires implementing changes to the current system baseline and deploying the updated baseline across the installed base using assigned automated mechanisms, i.e., automated deployment of baseline changes [cm-3.3].

- [cm-9] Configuration Management Plan requires developing, documenting, and implementing a configuration management plan that: addresses roles/responsibilities and CM processes; establishes a process for identifying and managing configuration items across the system life cycle; defines and places configuration items under CM; is reviewed and approved by assigned personnel/roles; and is protected from unauthorized disclosure and modification [cm-9].

If you need the specific values for the items marked “[Assignment: …]” (e.g., review frequency, circumstances that trigger review, which automated mechanisms, and which approving personnel/roles), those are not provided in the supplied controls and must be defined separately.

### Q14: What is the capital of France? (out-of-domain)

- **Expected:** safe fallback — returned the safe fallback ✓
- **Answer:** I could not find any NIST SP 800-53 control relevant to that question in the policy library, so I cannot answer it. Try rephrasing the question in terms of a security control, a control family, or a control ID such as AC-2.

### Q15: How do I bake a chocolate cake? (out-of-domain)

- **Expected:** safe fallback — returned the safe fallback ✓
- **Answer:** I could not find any NIST SP 800-53 control relevant to that question in the policy library, so I cannot answer it. Try rephrasing the question in terms of a security control, a control family, or a control ID such as AC-2.
