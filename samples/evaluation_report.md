# Evaluation report

Retrieval and answer quality of the NIST SP 800-53 policy pipeline over the hand-labeled golden set (`evaluation/golden_set.json`). Recall and graded NDCG@5 are computed directly from the golden qrels — pure, deterministic math with no external evaluator. Faithfulness and answer relevancy are LLM-judge scores (integer 1-5) from two Microsoft Agent Framework judge agents on the same Azure OpenAI deployment the pipeline uses.

Recall is shown two ways. **Exact-ID** credits only a retrieved control whose ID matches a labelled one. **Base-family** also credits a retrieved enhancement whose base control was labelled — `ia-2.6` counts toward the `ia-2` need — because a NIST SP 800-53 enhancement is a more specific form of its base control, so retrieving it genuinely answers the need. The exact-ID column is a strict lower bound; the base-family column is the fairer measure for this hierarchical catalog. NDCG@5 stays exact-ID, truncated at the pipeline's own top-k; it is computed in-house and is **not comparable** to the NDCG@3 the Azure AI `DocumentRetrievalEvaluator` reported in earlier committed runs.

## Aggregate

On-topic queries: 13 · Fallback queries: 2/2 returned the safe fallback · Invented citations: 0

| Metric | Exact-ID | Base-family |
|---|---|---|
| Recall | 0.451 | 0.653 |
| NDCG@5 | 0.490 | — |
| Faithfulness (1-5) | 5.00 | — |
| Answer relevancy (1-5) | 5.00 | — |

## Per-query results

### Q1: What controls apply to API security?

- **Plan:** API security protections for authentication, authorization, input validation, rate limiting, and secure transmission for APIs
- **Retrieved (5):** ac-4.22 (2.10), ac-20.1 (2.07), ia-5.6 (2.05), sa-4.7 (2.02), ia-9 (2.00)
- **Relevant (qrels):** sc-7(2), sc-8(2), ac-4(2), ac-3(1), ia-2(1)
- **Recall:** 0.000 exact-ID / 0.200 base-family · **NDCG@5:** 0.000
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ia-9, ia-5.6, ac-20.1, ac-4.22, sa-4.7]; invented [none]
- **Answer:** Applicable controls from the supplied set that relate to API security are:

- Service identification and authentication: APIs (as system services/applications) must be uniquely identified and authenticated before establishing communications with clients, other services, or devices ([ia-9]). This covers mutual identification and authentication of API endpoints and callers.

- Protection of authenticators: Any authenticators used by API clients (API keys, tokens, client certificates, etc.) must be protected commensurate with the sensitivity of the information the API grants access to ([ia-5.6]). This applies to storage, transmission, and handling of those authenticators.

- Limits on authorized use of external systems: If an API allows access from or through external systems, permit such use only after verifying the external system implements required controls per organization policies or after retaining approved connection/processing agreements ([ac-20.1]). This applies when APIs are consumed by or integrated with third-party or external-hosted systems.

- Access only across domains: If an API is used to give a single device access to resources across multiple security domains, ensure the API enforces controls to provide access while preventing information flow between those domains ([ac-4.22]). This is relevant for cross-domain API proxies or gateways.

- Acquisition of evaluated products: When acquiring commercial products or components (e.g., API gateways, TLS/cryptographic libraries) that enforce API security and rely on cryptography, prefer products evaluated against NIAP Protection Profiles where available, or require FIPS-validated/NSA-approved cryptographic modules if no profile exists ([sa-4.7]).

If you need mapping to specific API controls (e.g., rate limiting, input validation, TLS, scopes/authorization), those topics are not covered by the supplied controls.

### Q2: How should sensitive data be protected in cloud systems?

- **Plan:** protect sensitive data in cloud systems: encryption at rest and in transit, key management, data minimization, access controls, data loss prevention; data classification and handling of sensitive information
- **Retrieved (10):** ps-3.1 (2.48), ma-5.2 (2.41), sc-28 (2.37), ac-17.2 (2.31), sc-28.1 (2.30), sc-8.1 (2.28), sc-4.2 (2.22), pe-19.1 (2.21), mp-8.4 (2.20), ac-23 (2.20)
- **Relevant (qrels):** sc-28(2), sc-8(2), sc-13(2), sc-12(1)
- **Recall:** 0.250 exact-ID / 0.500 base-family · **NDCG@5:** 0.213
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ac-17.2, sc-8.1, sc-28, sc-28.1, ps-3.1, ma-5.2, sc-4.2, pe-19.1, mp-8.4, ac-23]; invented [none]
- **Answer:** Protecting sensitive data in cloud systems should follow the specific controls supplied:

- Use cryptography for data in transit and remote access. Implement cryptographic mechanisms to protect the confidentiality and integrity of remote access sessions [ac-17.2]. Also ensure cryptographic protection during transmission to prevent unauthorized disclosure and/or detect changes to transmitted information as required [sc-8.1].

- Use cryptography for data at rest. Protect the confidentiality and/or integrity of information at rest per the assigned scope [sc-28], and implement cryptographic mechanisms to prevent unauthorized disclosure and modification of information at rest on the designated system components or media [sc-28.1].

- Control and limit personnel access based on clearance and need. Verify that individuals accessing systems that process, store, or transmit classified information are cleared and indoctrinated to the highest classification level they will access [ps-3.1]. For maintenance and diagnostic personnel, verify they possess security clearances and formal access approvals for at least the highest classification level and compartments present on the system [ma-5.2].

- Prevent unauthorized cross-level transfer and leakage. When systems switch between different classification levels or security categories, prevent unauthorized information transfer via shared resources according to established procedures [sc-4.2].

- Protect physical emissions and components according to classification. Protect system components, related data communications, and networks in accordance with national emissions security policies and procedures tied to the security category or classification of the information [pe-19.1].

- Handle media appropriately. Downgrade system media that contains classified information before releasing it to individuals without the required authorizations [mp-8.4].

- Detect and mitigate data-mining threats. Employ assigned techniques to detect and protect specified data storage objects against unauthorized data mining [ac-23].

If you need these controls applied to a particular cloud service model (IaaS/PaaS/SaaS) or specific implementation steps (algorithms, key management, configuration), the supplied controls do not provide those details; they describe what must be done (for example, “implement cryptographic mechanisms”) but do not specify exact technologies, procedures, or parameter values.

### Q3: Summarise requirements for access control

- **Plan:** access control requirements for managing access to information systems and resources
- **Retrieved (5):** ac-3 (2.81), ac-3.3 (2.74), ac-3.4 (2.67), ac-3.12 (2.66), ac-20.1 (2.59)
- **Relevant (qrels):** ac-2(2), ac-3(2), ac-6(2), ac-1(1), ac-17(1)
- **Recall:** 0.200 exact-ID / 0.200 base-family · **NDCG@5:** 0.394
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ac-3]; invented [none]
- **Answer:** Summary of access-control requirements (based only on the supplied controls)

- Enforce approved logical-access authorizations to information and system resources in accordance with applicable access-control policies [ac-3].

- Support and enforce organization-defined mandatory access control (MAC) when specified:
  - The MAC policy must be uniformly enforced across covered subjects and objects [ac-3.3(a)].
  - The MAC policy can constrain subjects who have access so they cannot (as defined by the policy) pass information to unauthorized subjects/objects, grant their privileges, change specified security attributes, choose attributes/values for newly created or modified objects, or change access-control rules [ac-3.3(b)(1)–(5)].
  - The policy may explicitly allow particular subjects to be granted privileges that exempt them from some or all of those constraints [ac-3.3(c)].

- Support and enforce organization-defined discretionary access control (DAC) when specified:
  - Under DAC, a subject granted access may be allowed by policy to pass information to others, grant its privileges, change security attributes on subjects/objects/systems/components, select security attributes for new/revised objects, or change access-control rules [ac-3.4(a)–(e)].

- Require applications (during installation) to declare the access they need to specified system applications and functions, provide an enforcement mechanism to prevent unauthorized access, and require approval for access changes after installation [ac-3.12(a)–(c)].

- Limit authorized use of external systems for accessing or processing/storing/transmitting organization-controlled information by permitting such use only after either verification that the external system implements required controls per organizational security/privacy policies and plans, or retention of an approved system-connection or processing agreement with the entity hosting the external system [ac-20.1(a)–(b)].

If you need these requirements organized into operational controls or a checklist, tell me which format you prefer.

### Q4: What policies relate to logging and monitoring?

- **Plan:** logging and monitoring of system activity, audit logs, and security events
- **Retrieved (5):** au-2 (2.74), au-6.5 (2.46), ma-4.1 (2.43), au-12 (2.36), pe-6 (2.35)
- **Relevant (qrels):** au-2(2), au-6(2), au-12(2), si-4(2), au-3(1), ca-7(1)
- **Recall:** 0.333 exact-ID / 0.500 base-family · **NDCG@5:** 0.519
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [au-2, au-12, au-6.5, ma-4.1, pe-6]; invented [none]
- **Answer:** The supplied controls that relate to logging and monitoring are the following:

- Event logging policy and scope: AU-2 requires identification of what event types the system can log, coordination with other organizational entities to inform selection criteria, specification of which organization-defined event types are to be logged and their logging frequency or triggering situations, rationale for adequacy of those event types for after‑the‑fact investigations, and periodic review/update of the selected event types [au-2].

- Audit record generation and control: AU-12 requires the system to provide audit record generation for the event types identified in AU-2a on specified system components, permit authorized personnel/roles to select which event types are logged by specific components, and generate audit records that include the audit record content defined elsewhere (AU-3) for the AU-2c event types [au-12].

- Integrated analysis of audit records: AU-6.5 requires integrating analysis of audit records with other information sources (e.g., vulnerability scanning, performance data, system monitoring information, or other organization-defined data) to better identify inappropriate or unusual activity [au-6.5].

- Maintenance session logging and review: MA-4.1 requires logging organization-defined audit events for nonlocal maintenance and diagnostic sessions and reviewing those maintenance/diagnostic session audit records to detect anomalous behavior [ma-4.1].

- Physical access monitoring and review: PE-6 requires monitoring physical access to the facility housing the system to detect/respond to physical security incidents, reviewing physical access logs at an organization-defined frequency and upon specified events, and coordinating review/investigation results with the incident response capability [pe-6].

If you need these mapped into specific policy statements or wording for a security policy document, specify which controls you want turned into policy text and any organization-defined assignments (e.g., event types, frequencies, roles).

### Q5: What are the requirements for multi-factor authentication?

- **Plan:** multi-factor authentication requirements; requirement for authentication using multiple factors or authenticator types
- **Retrieved (7):** ia-2.6 (2.73), ia-5.16 (2.56), ia-2.5 (2.46), ac-7.4 (2.46), ia-5.5 (2.40), ia-12.5 (2.38), ia-10 (2.28)
- **Relevant (qrels):** ia-2(2), ia-2.1(2), ia-2.2(2), ia-5(1)
- **Recall:** 0.000 exact-ID / 1.000 base-family · **NDCG@5:** 0.000
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ia-2.6, ia-5.16, ia-2.5, ac-7.4, ia-5.5, ia-12.5, ia-10]; invented [none]
- **Answer:** From the supplied controls, the organization’s requirements for multi-factor authentication (MFA) are:

- Implement MFA for the specified access scopes (local/network/remote) and account types (privileged and/or non‑privileged) such that one factor is provided by a device separate from the system being accessed, and that separate device meets the organization’s assigned strength-of-mechanism requirements [ia-2.6].

Additional supporting and related requirements/considerations in the supplied controls (they do not change the core MFA requirement above, but affect issuance, backup/alternate factors, and special circumstances):

- Authenticators may need to be issued in person or by a trusted external party, with registration authority and authorization by designated personnel or roles, when those authenticator types are subject to that requirement [ia-5.16].

- If shared accounts or shared authenticators are used, each user must be individually authenticated before granting access to the shared account or resource (so MFA must still allow individual authentication prior to use) [ia-2.5].

- The organization may permit use of alternate authentication factors after a defined number of consecutive invalid logon attempts; if allowed, it must enforce an organizationally defined limit on consecutive invalid attempts using those alternate factors during a specified time period [ac-7.4].

- System developers/installers must provide unique authenticators or change default authenticators prior to delivery/installation (to avoid default shared credentials) [ia-5.5].

- Address confirmation for registration must be performed by delivering a registration code or notice of proofing via an out‑of‑band channel to verify the user’s physical or digital address of record (supports secure enrollment for authenticators) [ia-12.5].

- The organization must require supplemental/adaptive authentication techniques under specified circumstances (adaptive authentication) as assigned by the organization [ia-10].

If you need the exact selections (which access types, which account types, the device strength requirements, allowed alternate factors and their limits, or the specific supplemental techniques and circumstances), those are not specified in the supplied controls and must be provided/assigned by the organization.

### Q6: How should cryptographic keys be established and managed?

- **Plan:** cryptographic key management lifecycle, including generation, distribution, storage, use, rotation, and destruction
- **Retrieved (5):** sc-12.2 (2.13), sc-12.3 (2.09), sc-12 (2.04), sa-9.6 (2.01), ia-13.1 (1.99)
- **Relevant (qrels):** sc-12(2), sc-13(1), sc-17(1)
- **Recall:** 0.333 exact-ID / 0.333 base-family · **NDCG@5:** 0.319
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [sc-12, sc-12.2, sc-12.3, sa-9.6, ia-13.1]; invented [none]
- **Answer:** Establish and manage cryptographic keys as follows.

- Apply an organization-defined key management scheme for all cryptography used by the system: key generation, distribution, storage, protection, use, rotation, and retirement must follow the organization’s specified requirements [sc-12].

- Use approved technology and processes for symmetric keys: produce, control, and distribute symmetric keys using either NIST FIPS-validated or NSA-approved key management technology and processes as selected by the organization [sc-12.2].

- Use approved mechanisms for asymmetric keys per the organization’s selection: produce, control, and distribute asymmetric keys using the selected method (for example, NSA-approved key management technology and processes; prepositioned keying material; DoD-approved/issued Medium Assurance PKI certificates; DoD-approved/issued Medium Hardware Assurance PKI certificates with hardware security tokens protecting users’ private keys; or certificates issued in accordance with organization-defined requirements) [sc-12.3].

- Maintain exclusive organizational control of keys for material that is stored or transmitted via external systems: the organization must retain exclusive control of those cryptographic keys rather than delegating control to an external system or service [sa-9.6].

- Ensure keys that protect access tokens are generated, managed, and protected from disclosure and misuse: apply appropriate generation, management, and protection measures specifically for keys guarding access tokens [ia-13.1].

If you need, I can map these requirements into specific operational procedures (key generation, storage media, HSM usage, key rotation intervals, escrow/backup, compromise handling) consistent with the organization-defined requirements referenced above; note those details are not specified in the supplied controls.

### Q7: What controls govern incident response and reporting?

- **Plan:** incident response and reporting requirements
- **Retrieved (5):** ir-6 (2.96), ir-8 (2.72), sa-15.10 (2.66), ir-8.1 (2.47), ir-4 (2.44)
- **Relevant (qrels):** ir-4(2), ir-6(2), ir-8(2), ir-5(1)
- **Recall:** 0.750 exact-ID / 0.750 base-family · **NDCG@5:** 0.860
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ir-6, ir-8, sa-15.10, ir-8.1, ir-4]; invented [none]
- **Answer:** The supplied controls that govern incident response and reporting are:

- Incident Reporting: Require personnel to report suspected incidents to the organizational incident response capability within an organization-defined time period, and to report incident information to organization-defined authorities [ir-6].

- Incident Response Plan (organization-level): Require development of an incident response plan that provides a roadmap and organizational structure for the incident response capability; defines how the capability fits into the organization; tailors the plan to organizational requirements (mission, size, structure, functions); defines reportable incidents; provides metrics; defines required resources and management support; addresses incident information sharing; is reviewed/approved by assigned personnel at an assigned frequency; and explicitly designates responsibility for incident response to specified entities/roles. The plan must be distributed to incident response personnel, updated for system/organizational changes or lessons learned, have changes communicated to identified personnel/elements, and be protected from unauthorized disclosure and modification [ir-8].

- Incident Response Plan (system acquisition): Require system or service developers to provide, implement, and test an incident response plan for the system, component, or service [sa-15.10].

- Breach-specific additions: For breaches involving personally identifiable information, the incident response plan must include a process to determine whether notices to individuals or organizations are needed; an assessment process to determine harm and mitigation mechanisms; and identification of applicable privacy requirements [ir-8.1].

- Incident Handling: Implement an incident handling capability consistent with the incident response plan that covers preparation, detection and analysis, containment, eradication, and recovery; coordinate incident handling with contingency planning; incorporate lessons learned into procedures, training, and testing; and ensure incident handling rigor, intensity, scope, and results are comparable and predictable across the organization [ir-4].

If you need, I can map these controls to specific reporting timeframes, authorities, or roles—however, those values must be provided (they are organization-defined and assigned in the controls above).

### Q8: What are the requirements for security awareness and role-based training?

- **Plan:** security awareness and training program requirements for all users and roles; role-based security training requirements and content tailored to job responsibilities
- **Retrieved (7):** at-3 (2.83), at-3.2 (2.45), sa-16 (2.39), at-2 (2.39), at-1 (2.30), at-4 (2.20), pm-14 (2.05)
- **Relevant (qrels):** at-2(2), at-3(2), at-4(1)
- **Recall:** 1.000 exact-ID / 1.000 base-family · **NDCG@5:** 0.761
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [at-1, at-2, at-3, at-3.2, sa-16, at-4, pm-14]; invented [none]
- **Answer:** The supplied controls require an organizational program that establishes policies, procedures, defined roles, and records for both general security/privacy literacy and role‑based training, plus developer‑provided and physical security training. Key requirements derived from the supplied controls are:

- Policy, procedures, and governance:
  - Develop, document, and disseminate an awareness and training policy (organization-, mission-/process-, or system-level as selected) that covers purpose, scope, roles, responsibilities, management commitment, coordination, and compliance and is consistent with applicable authorities. Also provide procedures to implement the policy. Designate an official to manage policy/procedure development and dissemination. Review and update the policy and procedures at organization‑defined frequencies and after organization‑defined events [at-1].

- Literacy (general awareness) training:
  - Provide security and privacy literacy training to all system users (including managers, senior executives, and contractors) as part of initial onboarding and at an organization‑defined recurring frequency thereafter; also provide literacy training when required by system changes or after organization‑defined events. Use organization‑defined awareness techniques to increase awareness. Update literacy training and awareness content at organization‑defined frequencies and following organization‑defined events. Incorporate lessons learned from internal or external incidents or breaches into literacy training and awareness techniques [at-2].

- Role-based training:
  - Provide role‑based security and privacy training to personnel in organization‑defined roles and responsibilities before they are authorized to access systems/information or perform duties, and at an organization‑defined recurring frequency thereafter; and provide role‑based training when required by system changes. Update role‑based training content at organization‑defined frequencies and after organization‑defined events. Incorporate lessons learned from internal or external incidents or breaches into role‑based training [at-3].

- Physical security training:
  - Provide identified personnel or roles with initial and organization‑defined recurring training on the employment and operation of physical security controls [at-3.2].

- Developer‑provided training:
  - Require system/system component/service developers to provide specified training on correct use and operation of implemented security and privacy functions, controls, and mechanisms (training content to be assigned by the organization) [sa-16].

- Records, retention, and monitoring:
  - Document and monitor security and privacy training activities (both awareness and role‑based) and retain individual training records for an organization‑defined time period [at-4].

- Planning and alignment:
  - Implement and maintain a process to ensure organizational plans for security/privacy testing, training, and monitoring are developed, maintained, and executed; and review those plans for consistency with the organization’s risk management strategy and priorities for risk response [pm-14].

If you need specifics (e.g., the exact training frequency, which roles are covered, record retention periods, or which awareness techniques to use), those items are left as organization-defined assignments in the supplied controls and are not specified by the provided text.

### Q9: How should removable media and portable storage devices be controlled?

- **Plan:** Removable media and portable storage device controls for protection, labeling, access, and sanitization
- **Retrieved (5):** mp-4 (2.38), ac-19.5 (2.19), mp-6.3 (2.16), mp-7 (2.13), mp-6 (2.11)
- **Relevant (qrels):** mp-5(2), mp-7(2), mp-2(1), mp-4(1)
- **Recall:** 0.500 exact-ID / 0.500 base-family · **NDCG@5:** 0.444
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [mp-4, mp-6, mp-6.3, mp-7, ac-19.5]; invented [none]
- **Answer:** Control of removable media and portable storage devices should follow the organization-defined requirements in these controls.

- Physically control and securely store media types defined by the organization within organization-defined controlled areas, and protect those media until they are destroyed or sanitized using approved equipment, techniques, and procedures [mp-4].

- Sanitize organization-defined system media (including portable devices when applicable) prior to disposal, release out of organizational control, or release for reuse using organization-defined sanitization techniques and procedures; use sanitization mechanisms whose strength and integrity match the security category or classification of the information on the media [mp-6].

- For portable storage devices that will be connected to systems, apply nondestructive sanitization techniques prior to connection under the organization-defined circumstances (e.g., before introduction to sensitive environments) [mp-6.3].

- Restrict or prohibit the use of specified types of system media on specified systems or components using organization-defined controls; additionally, prohibit use of portable storage devices on organizational systems when such devices have no identifiable owner [mp-7].

- Employ full-device or container-based encryption (as selected by the organization) to protect the confidentiality and integrity of information on mobile devices [ac-19.5].

If you need specifics (what media types, which controlled areas, what sanitization techniques, what circumstances for nondestructive sanitization, or whether to select full-device vs container-based encryption and which use restrictions to apply), those are not specified in the supplied controls and must be defined by the organization per the placeholders in the controls.

### Q10: What controls address vulnerability scanning and flaw remediation?

- **Plan:** vulnerability scanning and automated vulnerability scanning; flaw remediation and vulnerability remediation processes
- **Retrieved (9):** ra-5 (2.96), si-2 (2.78), sa-15.7 (2.75), ca-2.2 (2.53), si-3.10 (2.51), sa-11 (2.45), si-2.4 (2.44), ra-5.6 (2.43), si-4.3 (2.41)
- **Relevant (qrels):** ra-5(2), si-2(2), si-3(1)
- **Recall:** 0.667 exact-ID / 1.000 base-family · **NDCG@5:** 0.867
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ra-5, ra-5.6, si-2, si-2.4, sa-15.7, sa-11, ca-2.2, si-3.10, si-4.3]; invented [none]
- **Answer:** The supplied controls that address vulnerability scanning and flaw remediation are:

- Vulnerability scanning and monitoring: [ra-5] (Vulnerability Monitoring and Scanning) — specifies monitoring and scanning frequency, use of vulnerability monitoring tools that support enumeration/formatting/impact measurement, analysis of scan reports, updating vulnerability definitions, and sharing scan information [ra-5] (and tool interoperability/automation details in [ra-5]b and update capability in [ra-5]f). Automated comparison of scan results is covered by [ra-5.6] (Automated Trend Analyses) which directs comparing results of multiple scans using automated mechanisms [ra-5.6].

- Flaw remediation (patching and corrections): [si-2] (Flaw Remediation) — requires identification, reporting, correction of system flaws; testing updates before installation; timely installation of security-relevant updates per an organization-defined timeframe; and incorporation of flaw remediation into configuration management [si-2]. Automated support for remediation is addressed by [si-2.4] (Automated Patch Management Tools), which mandates employing automated patch management tools for specified components [si-2.4].

- Developer-side vulnerability analysis and remediation integration: [sa-15.7] (Automated Vulnerability Analysis) — requires developers to perform automated vulnerability analyses, assess exploitability, determine mitigations, and deliver results to appropriate personnel [sa-15.7]. [sa-11] (Developer Testing and Evaluation) requires developers to implement testing/evaluation, produce evidence, implement a verifiable flaw remediation process, and correct discovered flaws (thus addressing remediation processes during development) [sa-11].

- Assessment inclusion and specialized scanning: [ca-2.2] (Specialized Assessments) — includes vulnerability scanning as a selectable specialized assessment type to be performed (announced or unannounced, at organization-defined frequency) as part of control assessments [ca-2.2].

- Malicious code and incident integration: [si-3.10] (Malicious Code Analysis) — calls for analysis tools/techniques for malicious code and incorporation of those results into incident response and flaw remediation processes (supporting remediation decisions) [si-3.10].

- Tool integration (supporting scanning/remediation workflows): [si-4.3] (Automated Tool and Mechanism Integration) — requires employing automated tools/mechanisms to integrate detection tools into control mechanisms, supporting automated detection/remediation workflows [si-4.3].

If you need a concise mapping (e.g., which to implement first or which cover scanning vs. patching), tell me and I’ll summarize accordingly. If you require controls beyond these, the supplied set does not cover them.

### Q11: What are the contingency planning and system backup requirements?

- **Plan:** Contingency planning and system backup requirements for information systems; CP family contingency plan development, backup, and recovery
- **Retrieved (9):** cp-9 (2.95), cp-2 (2.91), cp-6 (2.77), cp-9.6 (2.72), cp-9.3 (2.57), cp-6.2 (2.25), cp-4.4 (2.21), cp-1 (2.19), cp-10.4 (2.15)
- **Relevant (qrels):** cp-2(2), cp-9(2), cp-10(1)
- **Recall:** 0.667 exact-ID / 1.000 base-family · **NDCG@5:** 0.867
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [cp-1, cp-9.3, cp-6.2, cp-9.6, cp-4.4, cp-10.4]; invented [none]
- **Answer:** Summary of contingency planning and backup requirements based on the supplied controls:

Policy & procedures
- Establish and disseminate a documented contingency planning policy and implementing procedures, designate an official to manage them, and review/update the policy and procedures at organization-defined frequencies and after specified events [cp-1].  

Contingency plan content, distribution, and maintenance
- Develop a system contingency plan that: identifies essential mission/business functions and contingency requirements; provides recovery objectives, restoration priorities, and metrics; assigns contingency roles, responsibilities, and contact information; addresses maintaining essential functions during disruption and full restoration without control degradation; addresses sharing of contingency information; and is reviewed/approved by organization-defined personnel or roles [cp-2a.1–7].  
- Distribute plan copies to organization-defined key contingency personnel/elements and coordinate contingency planning with incident handling [cp-2b,c].  
- Review the contingency plan at organization-defined frequency, update it to reflect organizational/system/environment changes and problems encountered during implementation/testing, communicate changes to key personnel, and incorporate lessons learned from testing/training/actual events [cp-2d–g].  
- Protect the contingency plan from unauthorized disclosure and modification [cp-2h].

Backup requirements
- Conduct backups of user-level information in identified system components at organization-defined frequencies [cp-9a].  
- Conduct backups of system-level information at organization-defined frequencies [cp-9b].  
- Conduct backups of system documentation (including security/privacy documentation) at organization-defined frequencies [cp-9c].  
- Protect the confidentiality, integrity, and availability of backup information [cp-9d].

Backup storage and alternative facilities
- Maintain backup copies of critical system software and other security-related information in a separate facility or in a fire-rated container not collocated with the operational system [cp-9.3].  
- Establish an alternate storage site (including necessary agreements) to permit storage and retrieval of system backup information, and ensure the alternate site provides controls equivalent to the primary site [cp-6a,b].  
- Configure the alternate storage site to facilitate recovery in accordance with recovery time and recovery point objectives [cp-6.2].  
- Optionally, maintain a redundant secondary system that is not collocated with the primary system and can be activated without information loss or operational disruption to serve as a system backup [cp-9.6].

Recovery and testing
- Include full recovery and reconstitution of the system to a known state as part of contingency plan testing [cp-4.4].  
- Provide the capability to restore system components within organization-defined restoration time periods from configuration-controlled and integrity-protected information representing a known, operational state [cp-10.4].

If you want, specify the organization-defined items (e.g., backup frequencies, recovery time/objectives, approval roles, review frequencies) and I will produce concrete statements you can adopt.

### Q12: How is least privilege enforced and separation of duties maintained?

- **Plan:** least privilege enforcement for user and process access; separation of duties and role-based segregation of tasks
- **Retrieved (9):** ac-6 (3.18), sa-17.7 (2.69), sa-8.14 (2.41), ac-24.2 (2.39), cm-7.6 (2.38), ac-5 (2.34), sc-22 (2.11), ac-4.21 (1.85), au-16.3 (1.84)
- **Relevant (qrels):** ac-6(2), ac-5(2), ac-6.1(1)
- **Recall:** 0.667 exact-ID / 1.000 base-family · **NDCG@5:** 0.532
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ac-6, sa-17.7, sa-8.14, ac-5, ac-4.21, sc-22, ac-24.2, cm-7.6, au-16.3]; invented [none]
- **Answer:** Least privilege is enforced by implementing the principle that users and processes are granted only the accesses necessary to perform assigned tasks, and by requiring system components to be structured to make such constrained access feasible. Concretely: assign only necessary access rights to users/processes per task (least privilege) [ac-6]; require developers to structure security-relevant hardware/software/firmware so access can be controlled to support least-privilege operation [sa-17.7]; and apply least-privilege design to systems or components as part of acquisition/design activities [sa-8.14].

Separation of duties is maintained by identifying and documenting organizational duties and then defining system access authorizations to enforce those separations (so that conflicting duties cannot be performed by the same account) [ac-5]. Logical or physical separation of information flows is used where required to accomplish needed separations (for example, isolating workflows or data sets) [ac-4.21]. For services that provide critical infrastructure functions (name/address resolution), ensure role separation internally and externally and build fault-tolerance into those systems to support role-based separation of responsibilities [sc-22].

Additional supporting controls:
- Where access decisions must not rely on user/process identity, enforce decisions using organization-defined security/privacy attributes instead of identity when appropriate [ac-24.2].  
- Require user-installed software to run in confined physical or virtual environments with limited privileges, reducing the chance of privilege escalation that would violate least privilege/separation goals [cm-7.6].  
- Implement measures to disassociate individuals from audit information transmitted across organizational boundaries to preserve separation and privacy of audit data when sharing occurs [au-16.3].

If you need a specific implementation plan (e.g., role definitions, attribute lists, mechanisms for confinement, or examples of organization-defined attributes/techniques), those specifics are not provided in the controls above; they would need to be defined by the organization consistent with the controls cited.

### Q13: What controls apply to configuration management and baseline configurations?

- **Plan:** configuration management and baseline configuration controls; configuration change control and authorization
- **Retrieved (10):** cm-2 (3.08), cm-2.6 (2.71), cm-3 (2.67), cm-2.2 (2.52), cm-3.3 (2.45), cm-9 (2.39), cm-3.1 (2.39), cm-5 (2.39), sa-10 (2.39), cm-5.4 (2.37)
- **Relevant (qrels):** cm-2(2), cm-6(2), cm-3(1), cm-7(1)
- **Recall:** 0.500 exact-ID / 0.500 base-family · **NDCG@5:** 0.596
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [cm-2, cm-2.6, cm-2.2, cm-3, cm-3.1, cm-3.3, cm-5, cm-9, sa-10, cm-5.4]; invented [none]
- **Answer:** Controls that apply to configuration management and baseline configurations in the supplied set:

- Establishing and maintaining a system baseline configuration: [cm-2] requires developing, documenting, and maintaining under configuration control a current baseline configuration and reviewing/updating it at defined frequency, when specified circumstances occur, and when components are installed or upgraded [cm-2].

- Separating development/test baselines from operational baseline: [cm-2.6] requires maintaining a baseline configuration for development and test environments that is managed separately from the operational baseline [cm-2.6].

- Using automation to keep baselines accurate and current: [cm-2.2] requires maintaining currency, completeness, accuracy, and availability of the baseline using assigned automated mechanisms [cm-2.2].

- Controlling and documenting configuration changes: [cm-3] requires determining/documenting which types of changes are configuration-controlled; reviewing and approving proposed configuration-controlled changes with security/privacy impact consideration; documenting decisions; implementing approved changes; retaining records for an assigned time period; monitoring/reviewing change activities; and coordinating oversight through an assigned configuration change control element that convenes at assigned frequency or under assigned conditions [cm-3].

- Automated support for change management steps: [cm-3.1] requires use of assigned automated mechanisms to document proposed changes, notify approval authorities and request approvals, flag unapproved proposals after an assigned period, prohibit changes until approvals are received, document all changes, and notify personnel when approved changes are completed [cm-3.1].

- Automated change implementation and deployment of updated baselines: [cm-3.3] requires implementing changes to the current baseline and deploying the updated baseline across the installed base using assigned automated mechanisms [cm-3.3].

- Access restrictions for change activities: [cm-5] requires defining, documenting, approving, and enforcing physical and logical access restrictions associated with changes to the system [cm-5].

- Configuration management planning: [cm-9] requires developing, documenting, and implementing a configuration management plan that addresses roles/responsibilities and CM processes; establishes item identification and lifecycle management; defines and places configuration items under CM; is reviewed/approved by assigned personnel/roles; and protects the plan from unauthorized disclosure and modification [cm-9].

- Developer responsibilities for CM: [sa-10] requires system/component/service developers to perform configuration management across lifecycle phases; document/manage/control integrity of configuration item changes; implement only organization-approved changes; document approved changes and their security/privacy impacts; and track/report security flaws and resolutions to assigned personnel [sa-10].

- Dual authorization for certain changes: [cm-5.4] requires enforcing dual authorization for implementing changes to organization-defined system components and system-level information [cm-5.4].

If you need, I can map these to specific actions you should take (e.g., define review frequency, select automated mechanisms, assign change control board, retention period, and authorization rules) — note those specifics are left as assignments in the controls.

### Q14: What is the capital of France? (out-of-domain)

- **Expected:** safe fallback — returned the safe fallback ✓
- **Answer:** I could not find any NIST SP 800-53 control relevant to that question in the policy library, so I cannot answer it. Try rephrasing the question in terms of a security control, a control family, or a control ID such as AC-2.

### Q15: How do I bake a chocolate cake? (out-of-domain)

- **Expected:** safe fallback — returned the safe fallback ✓
- **Answer:** I could not find any NIST SP 800-53 control relevant to that question in the policy library, so I cannot answer it. Try rephrasing the question in terms of a security control, a control family, or a control ID such as AC-2.
