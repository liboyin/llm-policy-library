# Evaluation report

Retrieval and answer quality of the NIST SP 800-53 policy pipeline over the hand-labeled golden set (`evaluation/golden_set.json`). Precision, recall, and F1 are computed directly from the qrels; NDCG/XDCG/fidelity come from the Azure AI `DocumentRetrievalEvaluator`; groundedness and relevance are LLM-judge scores from the same Azure OpenAI deployment the pipeline uses.

Precision/recall/F1 are shown two ways. **Exact-ID** credits only a retrieved control whose ID matches a labelled one. **Base-family** also credits a retrieved enhancement whose base control was labelled — `ia-2.6` counts toward the `ia-2` need — because a NIST SP 800-53 enhancement is a more specific form of its base control, so retrieving it genuinely answers the need. The exact-ID column is a strict lower bound; the base-family column is the fairer measure for this hierarchical catalog. The graded NDCG/XDCG/fidelity stay exact-ID. Note the golden set grades relevance 1-2, so XDCG@3's achievable ceiling is ~50, not 100 (a perfectly-ranked query scores ~50).

## Aggregate

On-topic queries: 13 · Fallback queries: 2/2 returned the safe fallback · Invented citations: 0

| Metric | Exact-ID | Base-family |
|---|---|---|
| Precision | 0.267 | 0.535 |
| Recall | 0.508 | 0.705 |
| F1 | 0.339 | 0.579 |
| NDCG@3 | 0.553 | — |
| XDCG@3 | 25.8 | — |
| Fidelity | 0.589 | — |
| Groundedness (1-5) | 5.00 | — |
| Relevance (1-5) | 5.00 | — |

## Per-query results

### Q1: What controls apply to API security?

- **Plan:** API security, protection of application programming interfaces and related authentication, authorization, input validation, and logging
- **Retrieved (5):** ac-4.22 (2.13), ac-3.12 (2.09), ia-9 (2.08), sa-11.9 (2.02), pm-10 (2.00)
- **Relevant (qrels):** sc-7(2), sc-8(2), ac-4(2), ac-3(1), ia-2(1)
- **P/R/F1 (exact-ID):** 0.000 / 0.000 / 0.000
- **P/R/F1 (base-family):** 0.400 / 0.400 / 0.400
- **NDCG@3:** 0.000 · **XDCG@3:** 0.0 · **Fidelity:** 0.000 · **Holes:** 5
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [ia-9, ac-3.12, ac-4.22, sa-11.9, pm-10]; invented [none]
- **Answer:** Applicable controls for API security from the supplied set:

- Require authenticating services and applications that provide or consume APIs: use Service Identification and Authentication to uniquely identify and authenticate system services and applications before they establish communications with devices, users, or other services or applications [ia-9]. This covers ensuring API clients and backend services authenticate to each other.

- Enforce and limit application access requested by APIs: require applications to assert the access they need and provide enforcement to prevent unauthorized access, and approve changes after installation, per the Assert and Enforce Application Access control [ac-3.12]. This applies to API-enabled applications asserting required accesses and having controls to prevent privilege escalation or unauthorized API access [ac-3.12].

- Isolate access across security domains when APIs bridge multiple domains: if a single device accesses platforms, applications, or data in different security domains via APIs, provide access only while preventing information flow between domains, per Access Only (Access Control) [ac-4.22]. This addresses APIs that could inadvertently transfer data across domain boundaries [ac-4.22].

- Test APIs for security flaws during development: require developers to use interactive application security testing tools and document results, per Interactive Application Security Testing [sa-11.9]. This applies to dynamic and interactive testing of API implementations to find vulnerabilities [sa-11.9].

- Manage API security through authorization and risk processes: include API-bearing systems and their environments in the organization’s authorization processes, assign roles and responsibilities in risk management, and integrate authorization into the enterprise risk program, per Authorization Process [pm-10]. This covers governance, approval, and ongoing authorization of API deployments [pm-10].

If you need controls for specific API capabilities (e.g., rate limiting, input validation, encryption in transit, logging/monitoring, token management, or endpoint protection) those are not covered by the supplied controls. The above only addresses authentication of services [ia-9], application access assertions and enforcement [ac-3.12], cross-domain access restriction [ac-4.22], interactive security testing [sa-11.9], and governance/authorization [pm-10].

### Q2: How should sensitive data be protected in cloud systems?

- **Plan:** Protecting sensitive data in cloud systems through encryption and key management; Access control and separation of duties for protecting sensitive data in cloud environments; Data loss prevention, boundary protection, and monitoring for sensitive data in cloud services
- **Retrieved (14):** ac-17.2 (2.52), ac-5 (2.51), ia-13.1 (2.47), sc-12.2 (2.45), sc-12 (2.43), sc-8.1 (2.42), ac-4.21 (2.38), ac-4.22 (2.38), sc-7 (2.37), ac-6.4 (2.37), ca-2.2 (2.25), ac-20.1 (2.25), ac-4.25 (2.10), sa-9.8 (2.09)
- **Relevant (qrels):** sc-28(2), sc-8(2), sc-13(2), sc-12(1)
- **P/R/F1 (exact-ID):** 0.071 / 0.250 / 0.111
- **P/R/F1 (base-family):** 0.214 / 0.500 / 0.300
- **NDCG@3:** 0.000 · **XDCG@3:** 0.0 · **Fidelity:** 0.125 · **Holes:** 13
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [ac-17.2, sc-8.1, sc-12, sc-12.2, ia-13.1, ac-4.21, ac-6.4, ac-4.22, sc-7, ac-4.25, ac-20.1, ac-5, ca-2.2, sa-9.8]; invented [none]
- **Answer:** Protect sensitive data in cloud systems by applying the supplied controls as follows:

- Encrypt sensitive data in transit and for remote access. Implement cryptographic mechanisms that protect confidentiality and integrity of remote access sessions and transmissions (use mechanisms that prevent unauthorized disclosure and detect changes) [ac-17.2] [sc-8.1].

- Establish and manage cryptographic keys and key protection. Define and implement key establishment and management in accordance with organizational key management requirements [sc-12]. Ensure symmetric keys are produced, controlled, and distributed using approved key-management technology and processes (e.g., FIPS-validated or NSA-approved as selected) [sc-12.2]. Protect cryptographic keys that secure access tokens from disclosure and misuse [ia-13.1].

- Isolate and control information flows and processing domains. Separate information flows logically or physically to achieve required separations between domains, and provide separate processing domains for finer-grained allocation of privileges [ac-4.21] [ac-6.4]. Allow access from a single device to resources across different security domains while preventing cross-domain information flow where required [ac-4.22].

- Protect network boundaries and place public components in segregated subnetworks. Monitor and control communications at external and key internal interfaces, implement subnetworks for publicly accessible components that are physically or logically separated from internal networks, and connect to external networks only via managed boundary protection devices per your security architecture [sc-7].

- Sanitize data moving between security domains. When transferring information across domains, sanitize data to minimize spillage of sensitive information and delivery or augmentation of malicious content per policy [ac-4.25].

- Limit and verify external system use. Permit external systems to access or process organizational information only after verifying the external system implements required controls or after retaining approved connection/processing agreements [ac-20.1].

- Enforce separation of duties and access authorizations. Identify and document duties and define access authorizations to support separation of duties so no single individual can improperly expose or misuse sensitive data [ac-5].

- Assess and monitor specialized risks. Include specialized assessments (announced or unannounced) such as in‑depth monitoring, automated test cases, vulnerability scanning, insider threat assessment, and data leakage or loss assessment as part of control assessments [ca-2.2].

- Constrain data location if required. If organizational policy requires U.S. jurisdiction for processing and storage, restrict cloud processing and storage to facilities within the U.S. legal boundary [sa-9.8].

If you need design details (specific cryptographic algorithms, key lengths, or vendor configurations), those are not covered by the supplied controls; the controls describe what must be done (encryption, key management, separation, boundary protection, sanitization, assessments, jurisdictional limits) but do not specify the low‑level parameters or procedures.

### Q3: Summarise requirements for access control

- **Plan:** Access control requirements for information systems (account management, access enforcement, least privilege, separation of duties, session management)
- **Retrieved (5):** ac-6 (2.69), ac-3 (2.63), ac-2 (2.62), sa-17.7 (2.57), ac-3.3 (2.56)
- **Relevant (qrels):** ac-2(2), ac-3(2), ac-6(2), ac-1(1), ac-17(1)
- **P/R/F1 (exact-ID):** 0.600 / 0.600 / 0.600
- **P/R/F1 (base-family):** 0.800 / 0.600 / 0.686
- **NDCG@3:** 1.000 · **XDCG@3:** 50.0 · **Fidelity:** 0.778 · **Holes:** 2
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [ac-6, ac-3, sa-17.7, ac-3.3]; invented [none]
- **Answer:** Summary of access control requirements (based only on the supplied controls)

- Apply least privilege so users and processes get only the access necessary to perform assigned tasks [ac-6].

- Enforce approved logical access authorizations in accordance with organizational access control policies; systems must block access that is not authorized by those policies [ac-3].

- Manage accounts through a documented, auditable process that at minimum:
  - Defines allowed and prohibited account types [ac-2.a];
  - Assigns account managers [ac-2.b];
  - Specifies prerequisites/criteria for group and role membership [ac-2.c];
  - Documents authorized users, group/role membership, and access privileges (and required attributes) for each account [ac-2.d];
  - Requires approval by designated personnel/roles to create accounts [ac-2.e];
  - Creates, enables, modifies, disables, and removes accounts per policy, procedures, prerequisites, and criteria [ac-2.f];
  - Monitors account use [ac-2.g];
  - Notifies account managers/other designated personnel within defined time periods when accounts are no longer required, when users are terminated or transferred, and when usage or need-to-know changes [ac-2.h];
  - Authorizes access based on a valid access authorization, intended system usage, and any required attributes [ac-2.i];
  - Periodically reviews accounts for compliance with account management requirements [ac-2.j];
  - Provides a process to change shared/group authenticators when individuals leave the group (if such accounts are used) [ac-2.k];
  - Aligns account management with personnel termination and transfer processes [ac-2.l].

- Require system developers to design security-relevant hardware, software, and firmware so that access can be controlled using least-privilege principles (i.e., facilitate enforcement of least privilege by design) [sa-17.7].

- Where a mandatory access control (MAC) policy is used, enforce an organization-defined MAC uniformly across covered subjects and objects, ensuring the policy constrains subjects from:
  - Passing information to unauthorized subjects/objects;
  - Granting their privileges to others;
  - Changing specified security attributes on subjects, objects, systems, or components;
  - Choosing security attributes/values for newly created or modified objects;
  - Changing the access-control rules themselves;
  and permit explicit exceptions only where the policy grants specific privileges to subjects that are not limited by those constraints [ac-3.3].

If you need implementation specifics (for example, exact time periods, approval roles, or review frequency), those assignments were left as placeholders in the supplied controls and are not specified here [ac-2, ac-3.3].

### Q4: What policies relate to logging and monitoring?

- **Plan:** logging and monitoring of information system events and audit records; continuous monitoring and security event management; audit log retention and protection
- **Retrieved (15):** au-2 (2.91), au-9 (2.69), au-11 (2.69), ma-4.1 (2.64), au-12 (2.57), au-6.5 (2.43), si-7.8 (2.40), pm-31 (2.40), ca-7 (2.32), au-4 (2.30), au-9.3 (2.27), ca-7.4 (2.23), cm-5.1 (2.23), pm-14 (2.20), ca-7.3 (2.19)
- **Relevant (qrels):** au-2(2), au-6(2), au-12(2), si-4(2), au-3(1), ca-7(1)
- **P/R/F1 (exact-ID):** 0.200 / 0.500 / 0.286
- **P/R/F1 (base-family):** 0.400 / 0.667 / 0.500
- **NDCG@3:** 0.469 · **XDCG@3:** 25.5 · **Fidelity:** 0.500 · **Holes:** 12
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [au-2, au-12, au-4, au-11, au-9, au-9.3, ma-4.1, au-6.5, si-7.8, pm-31, ca-7, ca-7.4, ca-7.3, pm-14, cm-5.1]; invented [none]
- **Answer:** The supplied controls indicate these policies and policy-relevant requirements relate to logging and monitoring:

- Define what events are logged, why, and how often (event selection and rationale): [au-2] (AU-2a, AU-2c, AU-2d, AU-2e).  
- Specify which system components can generate audit records and who may select event types for those components; require generation of audit records with mandated content: [au-12] (AU-12a, AU-12b, AU-12c).  
- Allocate and manage audit log storage to meet retention requirements: [au-4].  
- Retain audit records for an organization-defined time period to support investigations and regulatory needs: [au-11].  
- Protect audit information and logging tools from unauthorized access, modification, and deletion, and alert designated personnel upon detection: [au-9] (AU-9a, AU-9b).  
- Use cryptographic mechanisms to protect integrity of audit information and tools: [au-9.3].  
- Log and review maintenance/diagnostic sessions for nonlocal maintenance and detect anomalies: [ma-4.1] (ma-4.1a, ma-4.1b).  
- Integrate audit-record analysis with other data sources (vulnerability scans, performance, system monitoring, etc.) to improve detection of unusual activity: [au-6.5].  
- Provide auditing capability and related actions when potential integrity violations are detected (generate audit record, alerts, etc.): [si-7.8].  
- Require organization-wide and system-level continuous monitoring strategies and programs that define metrics, monitoring/assessment frequencies, correlation/analysis, response actions, and reporting of security/privacy status: [pm-31] (pm-31a–f) and [ca-7] (ca-7a–g).  
- Include risk monitoring (effectiveness, compliance, change) as part of continuous monitoring: [ca-7.4].  
- Use trend analysis to adjust monitoring activities and frequencies based on empirical data: [ca-7.3].  
- Ensure testing, training, and monitoring plans are developed, maintained, executed, and reviewed for consistency with risk strategy: [pm-14] (pm-14a, pm-14b).  
- Enforce access restrictions with automated mechanisms and automatically generate audit records of enforcement actions: [cm-5.1] (cm-5.1a, cm-5.1b).

If you want, I can map these into specific policy statements (draft wording) derived only from the above controls.

### Q5: What are the requirements for multi-factor authentication?

- **Plan:** multi-factor authentication for user authentication and remote access; authentication factors and multi-factor configuration for privileged accounts
- **Retrieved (8):** ia-2.6 (2.97), ia-2.1 (2.53), ia-3 (2.48), ia-2.8 (2.44), ac-18.1 (2.42), ac-7.4 (2.41), ia-2.2 (2.35), ia-5.8 (2.23)
- **Relevant (qrels):** ia-2(2), ia-2.1(2), ia-2.2(2), ia-5(1)
- **P/R/F1 (exact-ID):** 0.250 / 0.500 / 0.333
- **P/R/F1 (base-family):** 0.625 / 1.000 / 0.769
- **NDCG@3:** 0.296 · **XDCG@3:** 15.3 · **Fidelity:** 0.583 · **Holes:** 6
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [ia-2.1, ia-2.2, ia-2.8, ac-18.1, ia-3, ia-5.8]; invented [none]
- **Answer:** The supplied controls require the following for multi-factor authentication (MFA):

- MFA must be implemented for access to privileged accounts [ia-2.1]. MFA must also be implemented for access to non-privileged accounts where specified [ia-2.2].

- For local, network, and/or remote access (as selected), one factor of the MFA must be provided by a device that is separate from the system being accessed, and that device must meet an assigned strength-of-mechanism requirement [ia-2.6(a)-(b)].

- Authentication mechanisms must be replay-resistant for access to privileged and/or non-privileged accounts as selected [ia-2.8].

- Wireless access must be protected using authentication (of users and/or devices as selected) and encryption, i.e., MFA for wireless access must be combined with encryption protections as defined under system selections [ac-18.1].

- Where primary authentication factors are exhausted after an organization-defined number of consecutive invalid logon attempts, the organization may allow use of alternate authentication factors; the organization must enforce an assigned limit on consecutive invalid attempts using the alternative factors within an assigned time period [ac-7.4(a)-(b)].

- Devices should be uniquely identified and authenticated before establishing local, remote, or network connections, which supports device-based factors in MFA [ia-3].

- The organization must implement assigned security controls to manage risks from individuals having accounts on multiple systems (which affects MFA deployment and account management) [ia-5.8].

If you need specifics not present here (for example: exact selections for local/network/remote, whether privileged/non-privileged, the required strength values for the separate device, exact replay-resistant methods, or numeric limits/time periods for invalid attempts), those details are not provided in the supplied controls.

### Q6: How should cryptographic keys be established and managed?

- **Plan:** cryptographic key establishment and management
- **Retrieved (5):** sc-12 (3.16), sc-12.3 (2.61), sc-12.2 (2.56), ia-5.14 (2.54), sc-28.3 (2.54)
- **Relevant (qrels):** sc-12(2), sc-13(1), sc-17(1)
- **P/R/F1 (exact-ID):** 0.200 / 0.333 / 0.250
- **P/R/F1 (base-family):** 0.600 / 0.333 / 0.429
- **NDCG@3:** 0.726 · **XDCG@3:** 25.5 · **Fidelity:** 0.538 · **Holes:** 4
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [sc-12, sc-12.2, sc-12.3, sc-28.3, ia-5.14]; invented [none]
- **Answer:** Use an organization-wide, documented key-establishment and key-management approach that covers generation, distribution, control, storage, and lifecycle actions for all cryptographic keys used by the system. Specifically:

- Define the key-management requirements that the organization will follow for all cryptography in the system (establish what algorithms, key sizes, lifetimes, roles, escrow/backup, recovery, and procedures are required) as required by [sc-12].

- For symmetric keys, produce, control, and distribute them using the selected validated technology and processes — e.g., NIST FIPS-validated or NSA-approved key management tools/processes — per the organization’s selection in [sc-12.2].

- For asymmetric keys, produce, control, and distribute them using the selected mechanism in [sc-12.3] (for example NSA‑approved key management technology and processes, prepositioned keying material, or approved/issued PKI certificates and hardware tokens as specified in the selection).

- Protect key storage: ensure cryptographic keys are stored in protected storage as defined by the organization’s selection in [sc-28.3] (for example hardware-protected key stores or other assigned safeguards).

- For any PKI-based authentication, manage PKI trust stores consistently across all platforms (networks, OSs, browsers, applications) using an organization-wide methodology so trust anchors and CA certificates are controlled and consistent, per [ia-5.14].

If you need a concrete set of the specific selections or the detailed requirements (e.g., which of the selectable options to use, exact safeguards for protected storage, or the full set of key-management lifecycle parameters), those are not specified in the supplied controls and must be defined by the organization as the assignments/selections referenced in [sc-12], [sc-12.2], [sc-12.3], and [sc-28.3].

### Q7: What controls govern incident response and reporting?

- **Plan:** Incident response planning, roles, and procedures for detecting, reporting, and handling security incidents; Requirements for incident reporting to external organizations and authorities, including escalation and notification timelines
- **Retrieved (7):** ir-8 (2.87), ir-4 (2.75), ir-1 (2.63), ir-6 (2.57), sa-15.10 (2.49), ir-8.1 (2.43), si-5 (2.17)
- **Relevant (qrels):** ir-4(2), ir-6(2), ir-8(2), ir-5(1)
- **P/R/F1 (exact-ID):** 0.429 / 0.750 / 0.545
- **P/R/F1 (base-family):** 0.571 / 0.750 / 0.649
- **NDCG@3:** 0.765 · **XDCG@3:** 40.8 · **Fidelity:** 0.875 · **Holes:** 4
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [ir-1, ir-4, ir-6, ir-8, ir-8.1, sa-15.10, si-5]; invented [none]
- **Answer:** The supplied controls that govern incident response and reporting are the following:

- IR-1 establishes the overall incident response policy and related procedures, requires designation of an official to manage policy/procedure development and dissemination, and mandates periodic review and update of the policy and procedures. This provides the foundational governance for incident response activities and roles [ir-1].

- IR-4 requires implementing an incident handling capability consistent with the incident response plan that covers preparation, detection and analysis, containment, eradication, and recovery; ties incident handling to contingency planning; incorporates lessons learned into procedures, training, and testing; and ensures consistent rigor and scope across the organization [ir-4].

- IR-6 governs incident reporting: it requires personnel to report suspected incidents to the organizational incident response capability within an organization-defined time period and requires reporting incident information to organization-defined authorities [ir-6].

- IR-8 requires development of an incident response plan that provides a roadmap, describes structure and organization, defines reportable incidents, provides metrics, designates responsibility, addresses incident information sharing, is reviewed/approved at an assigned frequency, and is distributed, updated, communicated to incident response personnel, and protected from unauthorized disclosure/modification [ir-8]. IR-8.1 adds specific plan content requirements for breaches involving personally identifiable information (process for notice, harm assessment and mitigation, and identification of applicable privacy requirements) [ir-8.1].

- SA-15.10 places a requirement on system/service developers to provide, implement, and test an incident response plan for the system/component/service they deliver, tying acquisition to incident response capability [sa-15.10].

- SI-5 requires receipt of external security alerts/advisories, generation and dissemination of internal alerts/advisories/directives to defined personnel/elements/external organizations, and implementation of security directives or notification of noncompliance—supporting timely awareness and notification related to incidents [si-5].

If you need specific assignments (e.g., the reporting time period, designated roles, approval frequency, list of authorities to report to), those are not specified in the supplied controls and must be defined by the organization per the placeholders in the controls.

### Q8: What are the requirements for security awareness and role-based training?

- **Plan:** Security awareness and training for all users (general security awareness program); Role-based security training tailored to specific duties and privileges
- **Retrieved (8):** at-3 (2.65), at-3.3 (2.46), at-2.4 (2.35), at-4 (2.33), at-2.6 (2.33), sa-16 (2.17), sr-11.1 (2.06), pm-14 (2.04)
- **Relevant (qrels):** at-2(2), at-3(2), at-4(1)
- **P/R/F1 (exact-ID):** 0.250 / 0.667 / 0.364
- **P/R/F1 (base-family):** 0.625 / 1.000 / 0.769
- **NDCG@3:** 0.556 · **XDCG@3:** 25.5 · **Fidelity:** 0.588 · **Holes:** 6
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [at-3, at-3.3, at-2.4, at-2.6, sa-16, sr-11.1, at-4, pm-14]; invented [none]
- **Answer:** The supplied controls require the organization to provide both general literacy/awareness training and targeted role-based training, with documentation, periodic updates, practical exercises, and incorporation of lessons learned. Specifically:

- Role-based training content and timing:
  - Provide role-based security and privacy training to organization-defined roles and responsibilities prior to authorizing access or performing duties, and thereafter at an organization-defined frequency; also provide training when system changes require it [at-3].  
  - Update role-based training content at an organization-defined frequency and after organization-defined events; incorporate lessons learned from internal or external incidents or breaches into role-based training [at-3].

- Practical reinforcement:
  - Include practical exercises in security and privacy training to reinforce objectives [at-3.3].

- Awareness topics:
  - Provide literacy training on recognizing suspicious communications and anomalous system behavior using organization-defined indicators of malicious code [at-2.4].  
  - Provide literacy training on the cyber threat environment and ensure current threat information is reflected in system operations [at-2.6].

- Training for specific supply-chain and acquisition roles:
  - Require system developers to provide training on correct use and operation of implemented security and privacy functions/controls as specified by the organization [sa-16].  
  - Train designated personnel/roles to detect counterfeit components (hardware, software, firmware) as assigned [sr-11.1].

- Records and retention:
  - Document and monitor all security and privacy training activities (both awareness and role-based) and retain individual training records for an organization-defined time period [at-4].

- Program-level planning and oversight:
  - Implement and maintain processes that ensure plans for testing, training, and monitoring are developed, maintained, and executed; and review those plans for consistency with the organization’s risk management strategy and priorities [pm-14].

If you need specific values (which roles get which training, the exact frequencies, events that trigger updates, indicators of malicious code, retention time period, or the content of developer-provided training), those must be defined by the organization—these controls require you to assign those parameters but do not specify them here [at-3], [at-3.3], [at-2.4], [at-2.6], [sa-16], [sr-11.1], [at-4], [pm-14].

### Q9: How should removable media and portable storage devices be controlled?

- **Plan:** Removable media and portable storage device protection and control
- **Retrieved (5):** mp-7 (2.54), ac-20.2 (2.52), mp-4 (2.48), ac-20.5 (2.42), mp-6.3 (2.42)
- **Relevant (qrels):** mp-5(2), mp-7(2), mp-2(1), mp-4(1)
- **P/R/F1 (exact-ID):** 0.400 / 0.500 / 0.444
- **P/R/F1 (base-family):** 0.400 / 0.500 / 0.444
- **NDCG@3:** 0.649 · **XDCG@3:** 30.1 · **Fidelity:** 0.500 · **Holes:** 3
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [mp-7, mp-4, ac-20.2, ac-20.5, mp-6.3]; invented [none]
- **Answer:** You should apply the supplied controls as follows.

- Restrict or prohibit use of specified media types on defined systems or components by applying appropriate controls, per the organizational selection in [mp-7] (Media Use). Also prohibit portable storage devices that have no identifiable owner from use in organizational systems [mp-7].

- Physically control and securely store organization-defined digital and non-digital media in organization-defined controlled areas, and protect those media until they are destroyed or sanitized using approved equipment, techniques, and procedures [mp-4].

- For organization-controlled portable storage devices used on external systems, either restrict their use by authorized individuals with the organization’s defined restrictions [ac-20.2], or prohibit such use entirely if that is the organizational decision [ac-20.5].

- Before connecting portable storage devices to systems, apply nondestructive sanitization techniques under the organization-defined circumstances (i.e., sanitize when those circumstances are met) [mp-6.3].

If you need specific assignments (the exact types of media, controlled areas, the selected restriction/prohibition options, the restrictions to apply to external systems, or the circumstances for nondestructive sanitization), those are not defined in the supplied controls and must be specified by the organization; the supplied controls only state the requirement types above.

### Q10: What controls address vulnerability scanning and flaw remediation?

- **Plan:** vulnerability scanning and flaw remediation
- **Retrieved (5):** ra-5 (2.79), si-2 (2.54), si-3.10 (2.54), ca-5 (2.40), sa-11 (2.39)
- **Relevant (qrels):** ra-5(2), si-2(2), si-3(1)
- **P/R/F1 (exact-ID):** 0.400 / 0.667 / 0.500
- **P/R/F1 (base-family):** 0.600 / 1.000 / 0.750
- **NDCG@3:** 0.907 · **XDCG@3:** 40.8 · **Fidelity:** 0.824 · **Holes:** 3
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [ra-5, si-2, si-3.10, ca-5, sa-11]; invented [none]
- **Answer:** The supplied controls that address vulnerability scanning and flaw remediation are:

- Vulnerability scanning and monitoring: [ra-5] requires monitoring and scanning for vulnerabilities in systems and hosted applications at organization-defined frequencies and when new vulnerabilities are reported, use of vulnerability monitoring tools and standards (enumeration, checklists/test procedures, impact measurement), analysis of scan reports, sharing scan information, and use of tools that can be readily updated [ra-5].

- Flaw remediation (identification, testing, installation, CM integration): [si-2] requires identifying, reporting, and correcting system flaws; testing software/firmware updates for effectiveness and side effects before installation; installing security-relevant updates within an organization-defined time period; and incorporating flaw remediation into the organizational configuration management process [si-2].

- Malicious code analysis supporting remediation: [si-3.10] requires employing tools/techniques to analyze malicious code characteristics/behavior and incorporating those analysis results into incident response and flaw remediation processes [si-3.10].

- Planning and tracking remediation actions: [ca-5] requires developing and maintaining a Plan of Action and Milestones (POA&M) to document planned remediation actions to correct weaknesses and reduce or eliminate known vulnerabilities, and updating the POA&M based on assessments and monitoring [ca-5].

- Developer responsibilities for remediation in acquisition/lifecycle: [sa-11] requires developers to implement ongoing control assessments, perform testing (unit/integration/system/regression as selected), produce evidence of testing, implement a verifiable flaw remediation process, and correct flaws found during testing/evaluation [sa-11].

If you need a concise mapping (e.g., which to use for scanning vs. patching vs. tracking), say so and I will provide it.

### Q11: What are the contingency planning and system backup requirements?

- **Plan:** Contingency planning and continuity of operations requirements for information systems; System backup and backup protection requirements for information systems
- **Retrieved (10):** cp-2 (2.89), cp-9 (2.78), cp-2.5 (2.66), cp-6 (2.61), cp-2.2 (2.54), cp-2.6 (2.49), cp-9.6 (2.48), cp-2.7 (2.43), cp-9.3 (2.34), sa-17.5 (2.30)
- **Relevant (qrels):** cp-2(2), cp-9(2), cp-10(1)
- **P/R/F1 (exact-ID):** 0.200 / 0.667 / 0.308
- **P/R/F1 (base-family):** 0.800 / 0.667 / 0.727
- **NDCG@3:** 0.907 · **XDCG@3:** 40.8 · **Fidelity:** 0.824 · **Holes:** 8
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [cp-2.5, cp-2.2, cp-2.6, cp-2.7, cp-9.3, cp-9.6]; invented [none]
- **Answer:** Contingency planning requirements
- Develop a documented contingency plan that: identifies essential mission and business functions and their contingency requirements; provides recovery objectives, restoration priorities, and metrics; assigns roles and contact information; addresses maintaining essential functions during disruption, eventual full system restoration without control degradation, and sharing of contingency information; and is reviewed and approved by organization‑defined personnel/roles [cp-2a.1][cp-2a.2][cp-2a.3][cp-2a.4][cp-2a.5][cp-2a.6][cp-2a.7].  
- Distribute plan copies to organization‑defined key contingency personnel and organizational elements [cp-2b].  
- Coordinate contingency planning with incident handling activities [cp-2c].  
- Review the contingency plan at the organization‑defined frequency and update it to reflect organizational, system, or environmental changes and issues discovered during testing/execution [cp-2d][cp-2e].  
- Communicate plan changes to key contingency personnel and organizational elements and incorporate lessons learned from testing, training, or actual events into future testing and training [cp-2f][cp-2g].  
- Protect the contingency plan from unauthorized disclosure and modification [cp-2h].  
- Plan for continuance of essential (or all, per selection) mission and business functions with minimal or no loss of operational continuity and sustain that continuity until full restoration at primary processing/storage sites [cp-2.5].  
- Conduct capacity planning to ensure necessary processing, telecommunications, and environmental support during contingency operations [cp-2.2].  
- Plan for transfer of essential (or all) mission and business functions to alternate processing and/or storage sites with minimal or no loss of continuity and sustain that continuity until restoration at primary sites [cp-2.6].  
- Coordinate the organization’s contingency plan with external service providers’ contingency plans so contingency requirements can be met [cp-2.7].

System backup requirements
- Conduct backups of user‑level information stored in the system components at the organization‑defined frequency [cp-9a].  
- Conduct backups of system‑level information at the organization‑defined frequency [cp-9b].  
- Conduct backups of system documentation, including security‑ and privacy‑related documentation, at the organization‑defined frequency [cp-9c].  
- Protect the confidentiality, integrity, and availability of backup information [cp-9d].  
- Maintain an alternate storage site (with necessary agreements) for storage and retrieval of backup information, ensuring controls at the alternate site are equivalent to the primary site [cp-6a][cp-6b].  
- Store backup copies of critical system software and other security‑related information in a separate facility or in a fire‑rated container not collocated with the operational system [cp-9.3].  
- Where applicable, maintain a redundant secondary system that is not collocated with the primary system and can be activated without loss of information or operational disruption [cp-9.6].

Design consideration
- Require system developers to use conceptually simple, precisely defined protection mechanisms in security‑relevant hardware/software/firmware to support dependable contingency and backup behavior [sa-17.5a][sa-17.5b].

If you need frequencies, selections (e.g., whether to plan for “essential” vs “all” functions), or the identities of personnel and storage locations, those are organization‑defined and must be supplied to fully specify the plan and backup schedule; the supplied controls require those assignments but do not themselves define them [cp-2b][cp-9a][cp-9b][cp-9c][cp-2.6].

### Q12: How is least privilege enforced and separation of duties maintained?

- **Plan:** least privilege enforcement for access to systems and data; separation of duties and conflict of interest controls
- **Retrieved (8):** ac-6 (2.95), sa-17.7 (2.84), sa-8.14 (2.55), cm-5.5 (2.29), ma-5.1 (2.29), ac-5 (2.24), ac-4.21 (1.93), au-16.3 (1.84)
- **Relevant (qrels):** ac-6(2), ac-5(2), ac-6.1(1)
- **P/R/F1 (exact-ID):** 0.250 / 0.667 / 0.364
- **P/R/F1 (base-family):** 0.250 / 1.000 / 0.400
- **NDCG@3:** 0.556 · **XDCG@3:** 25.5 · **Fidelity:** 0.824 · **Holes:** 6
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [ac-6, sa-17.7, sa-8.14, cm-5.5, ac-5, ac-4.21, ma-5.1, au-16.3]; invented [none]
- **Answer:** Least privilege is enforced by assigning and limiting access so users and processes receive only the accesses required to perform their assigned tasks, and by building systems to support that restriction. Specifically, the organization employs the principle of least privilege for users and processes [ac-6]; requires developers to structure security-relevant hardware, software, and firmware to facilitate access control consistent with least privilege [sa-17.7]; and mandates that least-privilege design be implemented in systems or system components per acquisition requirements [sa-8.14]. In production and operational environments, privileges to change system components and related information are restricted and reviewed at an organization-defined frequency to maintain appropriate privilege levels over time [cm-5.5].

Separation of duties is maintained by formally identifying and documenting duties and then defining system access authorizations to enforce those separations [ac-5]. Where separation of information flows is required, the organization separates flows either logically or physically using organization-defined mechanisms or techniques to achieve the required separations [ac-4.21]. For situations involving maintenance personnel without appropriate clearances or access, controls ensure such personnel are escorted and supervised and that volatile storage is sanitized or nonvolatile media removed (or alternate controls implemented) to prevent unauthorized access or duties conflicts during maintenance activities [ma-5.1].

When audit information crosses organizational boundaries, measures are implemented to disassociate individuals from that audit information, supporting privacy and preventing duty conflicts tied to audit data handling [au-16.3].

If you need specifics (e.g., the organization-defined mechanisms for logical separation, the frequency for privilege reviews, or the exact alternate controls for unsanitizable components), those are not defined in the supplied controls and would need to be specified by the organization.

### Q13: What controls apply to configuration management and baseline configurations?

- **Plan:** configuration management and baseline configurations; configuration management policy and procedures
- **Retrieved (9):** cm-1 (3.37), cm-2 (3.12), cm-2.6 (2.84), cm-9 (2.82), cm-3.3 (2.64), cm-11 (2.62), cm-2.2 (2.62), cm-3.1 (2.55), cm-6 (2.53)
- **Relevant (qrels):** cm-2(2), cm-6(2), cm-3(1), cm-7(1)
- **P/R/F1 (exact-ID):** 0.222 / 0.500 / 0.308
- **P/R/F1 (base-family):** 0.667 / 0.750 / 0.706
- **NDCG@3:** 0.351 · **XDCG@3:** 15.3 · **Fidelity:** 0.700 · **Holes:** 7
- **Groundedness:** 5.0/5 · **Relevance:** 5.0/5
- **Citations:** grounded [cm-1, cm-2, cm-2.6, cm-2.2, cm-3.1, cm-3.3, cm-6, cm-9, cm-11]; invented [none]
- **Answer:** The supplied controls that apply to configuration management and baseline configurations are the following (described in plain terms, citing each control):

- CM-1: Establish and maintain a configuration management policy and procedures, assign an official to manage them, and periodically review/update the policy and procedures [cm-1].

- CM-2: Create, document, and keep under configuration control a current system baseline configuration; review and update that baseline at a defined frequency, when certain circumstances require it, and when components are installed or upgraded [cm-2].

- CM-2.6: Maintain a separate baseline configuration for development and test environments, managed independently from the operational baseline [cm-2.6].

- CM-2.2: Use automated mechanisms to keep the baseline configuration current, complete, accurate, and available [cm-2.2].

- CM-3.1: Use automated mechanisms to document proposed changes, notify approval authorities and request approvals, flag unapproved changes within a time period, prohibit changes until approvals are received, record all changes, and notify personnel when approved changes are completed [cm-3.1].

- CM-3.3: Implement changes to the baseline and deploy the updated baseline across the installed base using automated mechanisms [cm-3.3].

- CM-6: Establish, document, and implement configuration settings that are the most restrictive consistent with operations (using common secure configurations); approve and document any deviations; and monitor/control changes to those settings [cm-6].

- CM-9: Develop, document, implement, and protect a configuration management plan that defines roles/responsibilities, processes for identifying and managing configuration items throughout the system life cycle, designates which items are under configuration management, and is reviewed/approved by designated personnel [cm-9].

- CM-11: Establish and enforce policies governing user-installed software and monitor compliance with those policies [cm-11].

These controls collectively cover policy/procedures, baseline creation and maintenance (including dev/test baselines), automation to maintain and implement baselines and manage change, configuration settings and approved deviations, configuration management planning, and user-installed software controls relevant to configuration posture [cm-1] [cm-2] [cm-2.6] [cm-2.2] [cm-3.1] [cm-3.3] [cm-6] [cm-9] [cm-11].

### Q14: What is the capital of France? (out-of-domain)

- **Expected:** safe fallback — returned the safe fallback ✓
- **Answer:** I could not find any NIST SP 800-53 control relevant to that question in the policy library, so I cannot answer it. Try rephrasing the question in terms of a security control, a control family, or a control ID such as AC-2.

### Q15: How do I bake a chocolate cake? (out-of-domain)

- **Expected:** safe fallback — returned the safe fallback ✓
- **Answer:** I could not find any NIST SP 800-53 control relevant to that question in the policy library, so I cannot answer it. Try rephrasing the question in terms of a security control, a control family, or a control ID such as AC-2.
