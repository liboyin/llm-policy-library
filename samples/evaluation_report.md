# Evaluation report

Retrieval and answer quality of the NIST SP 800-53 policy pipeline over the hand-labeled golden set (`evaluation/golden_set.json`). Recall and graded NDCG@5 are computed directly from the golden qrels — pure, deterministic math with no external evaluator. Faithfulness and answer relevancy are LLM-judge scores (integer 1-5) from two Microsoft Agent Framework judge agents on the same Azure OpenAI deployment the pipeline uses.

Recall is shown two ways. **Exact-ID** credits only a retrieved control whose ID matches a labelled one. **Base-family** also credits a retrieved enhancement whose base control was labelled — `ia-2.6` counts toward the `ia-2` need — because a NIST SP 800-53 enhancement is a more specific form of its base control, so retrieving it genuinely answers the need. The exact-ID column is a strict lower bound; the base-family column is the fairer measure for this hierarchical catalog. NDCG@5 stays exact-ID, truncated at the pipeline's own top-k; it is computed in-house and is **not comparable** to the NDCG@3 the Azure AI `DocumentRetrievalEvaluator` reported in earlier committed runs.

## Aggregate

On-topic queries: 13 · Fallback queries: 2/2 returned the safe fallback · Invented citations: 0

| Metric | Exact-ID | Base-family |
|---|---|---|
| Recall | 0.391 | 0.658 |
| NDCG@5 | 0.462 | — |
| Faithfulness (1-5) | 5.00 | — |
| Answer relevancy (1-5) | 5.00 | — |

## Per-query results

### Q1: What controls apply to API security?

- **Plan:** API security controls (authentication, authorization, input validation, rate limiting, logging); secure API development and acquisition (secure coding, supply chain, testing, API-specific requirements in contracts)
- **Retrieved (10):** sa-4 (2.17), ac-20.1 (2.16), sa-15 (2.10), sa-11.9 (2.07), ac-4.22 (2.06), ia-9 (2.00), ac-6.1 (2.00), sa-17 (1.99), ac-3.12 (1.99), sa-17.6 (1.99)
- **Relevant (qrels):** sc-7(2), sc-8(2), ac-4(2), ac-3(1), ia-2(1)
- **Recall:** 0.000 exact-ID / 0.400 base-family · **NDCG@5:** 0.000
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [sa-4, sa-15, sa-17, sa-17.6, sa-11.9, ac-20.1, ia-9, ac-3.12, ac-4.22, ac-6.1]; invented [none]
- **Answer:** Applicable controls for API security from the supplied set:

- Require secure acquisition and contractual requirements for APIs and API providers (include functional and assurance requirements, strength of mechanism, documentation, responsibilities, and acceptance criteria) per [sa-4].

- Require developers to follow a documented secure development process that explicitly addresses security and privacy requirements, standards, tools, tool configurations, and change management for APIs; and review that process against organization-defined security and privacy requirements per [sa-15].

- Require developers to produce API design specifications and a security/privacy architecture that align with the enterprise architecture, describe required security functionality and control allocation, and show how security functions work together for the API environment per [sa-17].

- Require developers to structure API-related hardware, software, and firmware to facilitate security testing per [sa-17.6].

- Require use of interactive application security testing tools to find and document application-layer flaws in APIs (dynamic/interactive testing) per [sa-11.9].

- Ensure external systems (third-party APIs or consumers) are permitted only after verification that controls are implemented as specified in organizational policies/plans or via approved connection/processing agreements per [ac-20.1].

- Require unique identification and authentication of system services and applications (this covers API endpoints and services) before establishing communications with devices, users, or other services/applications per [ia-9].

- Require applications (including API components) to declare required access during installation, provide enforcement mechanisms to prevent unauthorized access, and approve access changes after installation per [ac-3.12].

- Limit access from a single device to platforms, applications, or data across multiple security domains while preventing information flow between domains (relevant for multi-tenant or cross-domain API mediation) per [ac-4.22].

- Authorize access to security functions and security-relevant information for defined individuals and roles (apply to API management consoles, keys, and admin functions) per [ac-6.1].

If you need, I can map these controls to specific API practices (e.g., authentication approaches, token handling, rate limiting, logging), but the supplied controls above are the only ones I can use.

### Q2: How should sensitive data be protected in cloud systems?

- **Plan:** protecting sensitive data in cloud systems (data-at-rest, data-in-transit, encryption, key management, access controls, data loss prevention)
- **Retrieved (5):** ac-4.22 (2.47), ac-17.2 (2.41), sc-8.1 (2.34), sc-28.1 (2.29), sc-28 (2.27)
- **Relevant (qrels):** sc-28(2), sc-8(2), sc-13(2), sc-12(1)
- **Recall:** 0.250 exact-ID / 0.500 base-family · **NDCG@5:** 0.165
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [sc-8.1, sc-28.1, sc-28, ac-17.2, ac-4.22]; invented [none]
- **Answer:** To protect sensitive data in cloud systems, apply the supplied controls as follows:

- Protect data in transit with cryptography: implement cryptographic mechanisms to prevent unauthorized disclosure and/or detect changes to information during transmission, ensuring confidentiality and integrity of data moving to, from, and within cloud services [sc-8.1].

- Protect data at rest with cryptography: implement cryptographic mechanisms to prevent unauthorized disclosure and modification of sensitive information stored on cloud system components or media, thereby protecting confidentiality and integrity of information at rest [sc-28.1], and ensure the system's protections explicitly cover the assigned information at rest [sc-28].

- Protect remote access sessions with encryption: implement cryptographic mechanisms to protect the confidentiality and integrity of remote access sessions (for example, administrative or user sessions to cloud resources) so credentials and session data are not exposed or tampered with [ac-17.2].

- Limit cross-domain exposure: if providing access from a single device to resources in multiple security domains, enforce controls that allow access while preventing information flow between those different security domains to avoid accidental leakage across domains [ac-4.22].

If you need specifics (e.g., algorithms, key management, scope of "assigned information," or implementation details), the supplied controls do not specify those items; they only require implementing cryptographic protections and preventing inter-domain information flow as described above.

### Q3: Summarise requirements for access control

- **Plan:** access control requirements
- **Retrieved (5):** ac-3.12 (2.97), ac-3.3 (2.87), ac-19 (2.84), ac-6.2 (2.84), ac-20.1 (2.78)
- **Relevant (qrels):** ac-2(2), ac-3(2), ac-6(2), ac-1(1), ac-17(1)
- **Recall:** 0.000 exact-ID / 0.400 base-family · **NDCG@5:** 0.000
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ac-3.12, ac-3.3, ac-19, ac-6.2, ac-20.1]; invented [none]
- **Answer:** Summary of access control requirements based only on the supplied controls:

- Applications must declare and enforce their required access at installation, provide mechanisms that prevent unauthorized access, and require approval for any access changes after installation [ac-3.12].

- A mandatory access control (MAC) policy must be defined and enforced uniformly across covered subjects and objects. That policy must constrain subjects (for example) from passing information to unauthorized entities, granting privileges to others, altering specified security attributes, choosing security attributes for new/modified objects, or changing access rules—unless the policy explicitly grants particular subjects privileges that exempt them from one or more of those constraints [ac-3.3].

- Establish and document configuration, connection, and implementation requirements and guidance for organization-controlled mobile devices (including when they operate outside controlled areas), and authorize those devices before they connect to organizational systems [ac-19].

- Require that users who have accounts or roles with access to security functions or security-relevant information use non‑privileged accounts or roles when performing non‑security functions (separation of privileged use) [ac-6.2].

- Allow use of external systems to access organizational systems or to process/store/transmit organizational information only after (a) verification that the external system implements required controls per organizational policies/plans, or (b) retention of an approved connection/processing agreement with the external system’s hosting entity [ac-20.1].

If you need these requirements grouped into implementation actions or a checklist, tell me and I will organize them that way.

### Q4: What policies relate to logging and monitoring?

- **Plan:** audit logging and log review requirements; continuous monitoring and security event monitoring
- **Retrieved (10):** au-2 (2.65), ma-4.1 (2.54), pm-31 (2.44), ra-5.8 (2.39), ca-7 (2.38), au-12.3 (2.34), au-5.2 (2.33), ca-7.4 (2.27), ca-7.3 (2.21), ca-5 (2.21)
- **Relevant (qrels):** au-2(2), au-6(2), au-12(2), si-4(2), au-3(1), ca-7(1)
- **Recall:** 0.333 exact-ID / 0.500 base-family · **NDCG@5:** 0.433
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [au-2, ma-4.1, au-5.2, au-12.3, ra-5.8, pm-31, ca-7, ca-7.4, ca-7.3, ca-5]; invented [none]
- **Answer:** The supplied controls that relate to logging and monitoring are the following (summarized with their key logging/monitoring policy elements and responsibilities):

- Event Logging: [au-2] — Policies must identify which event types the system can log, coordinate logging requirements with other organizational entities, specify which organization-defined event types (and their logging frequency or triggering situations) will actually be logged, provide a rationale that the selected events support after‑the‑fact investigations, and require periodic review/update of the selected event types [au-2].

- Maintenance-session logging and review: [ma-4.1] — Policies must require logging of organization-defined audit events for nonlocal maintenance and diagnostic sessions and review those maintenance/diagnostic audit records to detect anomalous behavior [ma-4.1].

- Real-time alerting for logging failures: [au-5.2] — Policies must provide for alerts within an organization-defined real‑time period to designated personnel/roles/locations when specified audit logging failure events occur [au-5.2].

- Ability to change logging configuration: [au-12.3] — Policies must permit designated individuals/roles to change what is logged on specified system components, based on selectable event criteria and within defined time thresholds [au-12.3].

- Historic log review for risk assessment: [ra-5.8] — Policies should require review of historic audit logs to determine whether a discovered vulnerability has previously been exploited within an organization-defined time period [ra-5.8].

- Organization-wide continuous monitoring strategy: [pm-31] — Policies must establish organization-wide metrics to monitor, set monitoring and assessment frequencies, require ongoing monitoring and correlation/analysis of monitoring and control-assessment data, define response actions, and require reporting of system security/privacy status to defined personnel/roles at defined frequencies [pm-31].

- System-level continuous monitoring: [ca-7] — Policies must require a system-level continuous monitoring strategy aligned with the organization strategy: define system-level metrics, set monitoring and assessment frequencies, perform ongoing control assessments and monitoring, correlate and analyze results, take response actions, and report system status to defined personnel/roles at defined frequencies [ca-7].

- Risk monitoring as part of continuous monitoring: [ca-7.4] — Policies must ensure risk monitoring (effectiveness, compliance, and change monitoring) is integral to the continuous monitoring strategy [ca-7.4].

- Trend analysis to tune monitoring: [ca-7.3] — Policies must employ trend analyses of monitoring and assessment data to determine whether monitoring activities, their frequency, or methods should be modified [ca-7.3].

- Plan of Action and Milestones updates from monitoring: [ca-5] — Policies must require development of a plan of action and milestones to document remediation of weaknesses found during assessments and continuous monitoring, and require periodic updates to that plan based on control assessments, audits, and continuous monitoring findings [ca-5].

If you need these translated into specific policy statements or templates (e.g., required log types, alert thresholds, monitoring frequencies, reporting recipients), specify which system or organizational scope and which assignments (metrics, frequencies, personnel, event types) you want filled in; otherwise the controls above define the policy topics but leave those assignments organization‑defined [au-2], [au-12.3], [au-5.2], [pm-31], [ca-7].

### Q5: What are the requirements for multi-factor authentication?

- **Plan:** multi-factor authentication requirements
- **Retrieved (5):** ia-2.6 (2.73), ia-5.16 (2.48), ac-7.4 (2.46), ia-5.5 (2.40), ia-12.5 (2.38)
- **Relevant (qrels):** ia-2(2), ia-2.1(2), ia-2.2(2), ia-5(1)
- **Recall:** 0.000 exact-ID / 1.000 base-family · **NDCG@5:** 0.000
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ia-2.6, ia-5.16, ia-5.5, ac-7.4, ia-12.5]; invented [none]
- **Answer:** The supplied controls require multi-factor authentication to be implemented as follows:

- Multi-factor authentication must be used for the specified access type(s) (local, network, remote) and account type(s) (privileged and/or non‑privileged) with the additional requirements that one factor be provided by a device separate from the system being accessed and that the separate device meet the organization’s assigned strength-of-mechanism requirements [ia-2.6].

- Issuance of authenticators (types or specific authenticators as assigned) must occur either in person or via a trusted external party prior to registration authority approval and authorized personnel/roles, per the organization’s selections [ia-5.16].

- Developers and installers must provide unique authenticators or change default authenticators prior to delivery and installation (so default/shared credentials are not used) [ia-5.5].

- Where an alternate authentication factor is allowed after a defined number of consecutive invalid logon attempts, the organization must (a) specify which alternate factors may be used and (b) enforce an organization‑defined limit on the number of consecutive invalid logon attempts using the alternate factors within an assigned time period [ac-7.4].

- The organization must verify users’ address of record (physical or digital) by delivering a registration code or notice of proofing via an out‑of‑band channel as part of confirmation of identity/registration [ia-12.5].

If you need these requirements framed into a policy statement or specific selections filled (e.g., which access/account types, strength requirements, number/time limits, or whether issuance is in person vs trusted party), specify those choices and I will produce the corresponding policy text.

### Q6: How should cryptographic keys be established and managed?

- **Plan:** cryptographic key establishment and management; key management lifecycle: generation, distribution, storage, rotation, destruction
- **Retrieved (6):** sc-12 (3.16), sc-12.3 (2.61), sc-12.2 (2.56), ia-5.14 (2.54), sc-28.3 (2.54), sc-36 (1.89)
- **Relevant (qrels):** sc-12(2), sc-13(1), sc-17(1)
- **Recall:** 0.333 exact-ID / 0.333 base-family · **NDCG@5:** 0.639
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [sc-12.2, sc-12.3, sc-28.3, sc-12, ia-5.14, sc-36]; invented [none]
- **Answer:** Use a documented key-management process that covers key generation/production, control, distribution, storage, and any organization-specific requirements for keys and trust stores.

- For symmetric keys, produce, control, and distribute them using NIST FIPS-validated or NSA‑approved key management technology and processes as selected by the organization [sc-12.2].  
- For asymmetric keys, produce, control, and distribute them using the organization’s selected mechanism (for example NSA‑approved key management technology/processes, prepositioned keying material, DoD/Medium Assurance PKI certificates, hardware tokens protecting private keys, or organization-defined certificate issuances) [sc-12.3].  
- Ensure cryptographic keys are stored in protected storage; implement the assigned safeguards and/or use a hardware-protected key store per organization selection [sc-28.3].  
- Establish and manage cryptographic keys whenever cryptography is employed in the system, in accordance with the organization’s defined key management requirements (i.e., the assignment called out in the control) [sc-12].  
- For PKI-based authentication, employ an organization-wide methodology to manage the content of PKI trust stores across all platforms (networks, OS, browsers, applications) so trust anchors are consistent and controlled [ia-5.14].  
- Where relevant, distribute processing and storage components (including those handling keys) across multiple physical locations or logical domains as defined by the organization to support resilience and separation of duties [sc-36].

If you want, provide the organization’s specific selections/assignments (e.g., use NIST FIPS-validated key management, require hardware-protected key stores, and a centralized PKI trust-store process) and I will convert this into a short policy statement.

### Q7: What controls govern incident response and reporting?

- **Plan:** Incident response and reporting requirements
- **Retrieved (5):** ir-6 (2.96), ir-8 (2.72), sa-15.10 (2.66), ir-8.1 (2.47), ir-4 (2.44)
- **Relevant (qrels):** ir-4(2), ir-6(2), ir-8(2), ir-5(1)
- **Recall:** 0.750 exact-ID / 0.750 base-family · **NDCG@5:** 0.860
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ir-6, ir-8, ir-4, ir-8.1, sa-15.10]; invented [none]
- **Answer:** The controls that govern incident response and reporting in the supplied set are:

- Incident Reporting: Require personnel to report suspected incidents to the organizational incident response capability within an organization-defined time period, and report incident information to designated authorities [ir-6].

- Incident Response Plan: Develop and maintain a documented incident response plan that provides a roadmap for the capability; describes structure and organization; explains how the capability fits into the organization; addresses organizational uniqueness (mission, size, structure, functions); defines reportable incidents; provides metrics; defines needed resources and management support; addresses sharing of incident information; is reviewed/approved by assigned personnel at an assigned frequency; and explicitly designates responsibility for incident response to assigned entities/roles. The plan must be distributed to incident response personnel, updated for system/organizational changes or problems encountered during implementation/testing, have changes communicated to defined personnel/elements, and be protected from unauthorized disclosure/modification [ir-8].

- Incident Handling: Implement an incident handling capability aligned with the incident response plan that includes preparation, detection and analysis, containment, eradication, and recovery; coordinate incident handling with contingency planning; incorporate lessons learned into procedures, training, and testing; and ensure incident handling activities are comparable and predictable across the organization [ir-4].

- Breach-specific plan content: For breaches involving personally identifiable information (PII), include in the incident response plan processes to determine whether notice to individuals or organizations is needed, an assessment process for the extent of harm and mitigation mechanisms, and identification of applicable privacy requirements [ir-8.1].

- Acquisition requirement: Require developers of systems, components, or services to provide, implement, and test an incident response plan as part of system and service acquisition [sa-15.10].

If you need the specific assignments (e.g., the time period for reporting, the authorities to report to, which personnel must review/approve the plan, or the distribution list), those are defined as organization-specific assignments in the controls and are not provided in the supplied text.

### Q8: What are the requirements for security awareness and role-based training?

- **Plan:** security awareness and role-based training requirements
- **Retrieved (5):** at-3 (3.01), at-3.2 (2.47), sa-16 (2.43), at-2 (2.39), at-4 (2.39)
- **Relevant (qrels):** at-2(2), at-3(2), at-4(1)
- **Recall:** 1.000 exact-ID / 1.000 base-family · **NDCG@5:** 0.863
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [at-3.2, sa-16]; invented [none]
- **Answer:** Role-based security and privacy training requirements (from the supplied controls):

- Provide role-based security and privacy training to personnel in organization-defined roles and responsibilities before they are authorized to access the system, information, or perform assigned duties, and at an organization-defined frequency thereafter. Also provide role-based training when required by system changes [at-3.a.1].  
- Update role-based training content at an organization-defined frequency and after organization-defined events [at-3.b].  
- Incorporate lessons learned from internal or external security incidents or breaches into role-based training [at-3.c].  
- For personnel or roles that operate physical security controls, provide initial and organization-defined recurring training in the employment and operation of those physical controls [at-3.2].  
- Where systems, components, or services are developed by an external developer, require that the developer provide training on correct use and operation of the implemented security and privacy functions, controls, and mechanisms (content to be specified by the organization) [sa-16].  
- Provide security and privacy literacy training to system users (including managers, senior executives, and contractors) as part of initial onboarding and at an organization-defined recurring frequency thereafter, and when required by system changes or after organization-defined events [at-2.a]. Use organization-defined awareness techniques to increase awareness and update literacy content at an organization-defined frequency and following organization-defined events; incorporate lessons learned from incidents or breaches into literacy training [at-2.b–d].  
- Document and monitor all information security and privacy training activities, including role-based training, and retain individual training records for an organization-defined time period [at-4.a–b].

If you want, I can produce a concise checklist mapping these requirements to specific organizational actions and propose typical values for the organization-defined frequencies, events, techniques, and retention periods.

### Q9: How should removable media and portable storage devices be controlled?

- **Plan:** removable media and portable storage device controls
- **Retrieved (5):** mp-7 (2.27), mp-4 (2.23), ac-20.2 (2.20), mp-6.3 (2.12), mp-4.2 (2.09)
- **Relevant (qrels):** mp-5(2), mp-7(2), mp-2(1), mp-4(1)
- **Recall:** 0.500 exact-ID / 0.500 base-family · **NDCG@5:** 0.628
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [mp-7, mp-4, mp-4.2, ac-20.2, mp-6.3]; invented [none]
- **Answer:** Control and handling of removable media and portable storage devices should follow the organization’s MP and AC requirements as supplied:

- Restrict or prohibit use by policy: explicitly restrict or prohibit particular types of system media on specified systems or components using defined controls, and prohibit portable storage devices on organizational systems when devices have no identifiable owner [mp-7].

- Physical control and secure storage: physically control and store organization-defined types of digital and non-digital media within organization-defined controlled areas, and protect those media until they are destroyed or sanitized using approved equipment, techniques, and procedures [mp-4].

- Access controls for storage areas: restrict access to media storage areas with automated mechanisms and log both access attempts and access granted as defined by the organization [mp-4.2].

- Restrict use on external systems: restrict the use of organization-controlled portable storage devices by authorized individuals when those devices are used on external systems, according to the organization’s defined restrictions [ac-20.2].

- Nondestructive sanitization before connecting: when required by the organization-defined circumstances, apply nondestructive sanitization techniques to portable storage devices prior to connecting them to the system [mp-6.3].

If you need these items translated into specific operational rules (e.g., which media types are prohibited, what sanitization circumstances trigger mp-6.3, what automated mechanisms to use, or what restrictions apply under ac-20.2), those assignments are not specified in the supplied controls and would need to be defined by the organization.

### Q10: What controls address vulnerability scanning and flaw remediation?

- **Plan:** vulnerability scanning and flaw remediation
- **Retrieved (5):** ra-5 (2.79), si-2 (2.54), si-3.10 (2.54), ca-5 (2.40), sa-11 (2.39)
- **Relevant (qrels):** ra-5(2), si-2(2), si-3(1)
- **Recall:** 0.667 exact-ID / 1.000 base-family · **NDCG@5:** 0.867
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ra-5, si-2, si-3.10, ca-5, sa-11]; invented [none]
- **Answer:** The supplied controls that address vulnerability scanning and flaw remediation are:

- Vulnerability scanning and monitoring: [ra-5] requires monitoring and scanning for vulnerabilities in systems and hosted applications at organization-defined frequency or when new vulnerabilities are identified, use of interoperable/automatable vulnerability tools and standards (enumeration, checklist/test formatting, impact measurement), analysis of scan reports, updating scanned vulnerabilities capability, and sharing scan findings with appropriate personnel [ra-5a–f].

- Flaw remediation (identification, testing, installation, and process integration): [si-2] requires identification, reporting, and correction of system flaws; testing software/firmware updates for effectiveness and side effects before installation; installing security-relevant updates within an organization-defined time period; and incorporating flaw remediation into configuration management [si-2a–d].

- Malicious-code analysis supporting remediation: [si-3.10] mandates use of tools/techniques to analyze malicious code characteristics and incorporation of those analysis results into incident response and flaw remediation processes, which supports remediation decisions [si-3.10a–b].

- Planning and tracking remediation actions: [ca-5] requires development and periodic update of a Plan of Action and Milestones documenting planned remediation actions to correct weaknesses and reduce/eliminate known vulnerabilities, based on assessments and continuous monitoring [ca-5a–b].

- Developer responsibilities for remediation during acquisition/SDLC: [sa-11] requires developers to implement ongoing control assessments, perform testing (unit/integration/system/regression as selected), produce evidence, implement a verifiable flaw remediation process, and correct flaws found during testing/evaluation [sa-11a–e].

If you need a concise mapping (e.g., which controls to use for scanning vs. patching vs. tracking), tell me and I will list it.

### Q11: What are the contingency planning and system backup requirements?

- **Plan:** contingency planning and system backups: develop and test contingency plans, backups, alternate processing sites, recovery time and point objectives, and restoration procedures
- **Retrieved (5):** cp-4.2 (2.80), cp-2 (2.74), cp-4 (2.61), cp-9.2 (2.59), cp-6.2 (2.58)
- **Relevant (qrels):** cp-2(2), cp-9(2), cp-10(1)
- **Recall:** 0.333 exact-ID / 0.667 base-family · **NDCG@5:** 0.335
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [cp-9.2, cp-6.2]; invented [none]
- **Answer:** The supplied controls require the organization to create, maintain, test, and protect a documented contingency plan that defines recovery objectives and supports backup/restoration activities.

Key requirements from the supplied controls:

- Develop a documented contingency plan that:
  - Identifies essential mission/business functions and contingency requirements; provides recovery objectives, restoration priorities, and metrics; assigns roles/responsibilities and contact information; addresses maintaining essential functions during disruption and eventual full restoration without degradation of original controls; addresses sharing contingency information; is reviewed and approved by organization-defined personnel [cp-2 a.1–a.7]. Protect the contingency plan from unauthorized disclosure or modification [cp-2 h].
  - Distribute copies of the plan to organization-defined key contingency personnel/elements and communicate plan changes to those personnel/elements [cp-2 b, f].
  - Coordinate contingency planning with incident handling activities; review the plan at organization-defined frequency and update to reflect organizational, system, or environment changes and lessons learned from testing/execution [cp-2 c, d, e, g].

- Test the plan regularly:
  - Conduct contingency plan tests at an organization-defined frequency using organization-defined test types; review test results and initiate corrective actions as needed [cp-4 a–c].
  - When testing restoration, use sampling of backup information to restore selected system functions as part of plan testing [cp-9.2].

- Alternate/backup site requirements:
  - Test contingency operations at the alternate processing site to familiarize personnel with the facility/resources and to evaluate the site’s capabilities to support contingency operations [cp-4.2 a–b].
  - Configure alternate storage/site to support recovery operations in accordance with the defined recovery time objectives (RTOs) and recovery point objectives (RPOs) [cp-6.2].

What these controls do not specify (and thus are not answered here):
- Specific frequencies for reviews and tests, exact RTO/RPO values, detailed backup schedules or retention periods, technical backup procedures, encryption/handling of backups, or specific corrective-action processes—those are organization-defined items referenced in the controls ([cp-2 d], [cp-4 a], [cp-6.2]).

### Q12: How is least privilege enforced and separation of duties maintained?

- **Plan:** least privilege enforcement, role-based access control, account privilege management; separation of duties, segregation of duties, conflict of interest controls
- **Retrieved (10):** ac-6 (2.76), sa-17.7 (2.46), ac-2.7 (2.43), ac-3.7 (2.39), ac-5 (2.31), cm-7.6 (2.23), sc-7.20 (1.95), ac-4.21 (1.93), au-16.3 (1.83), sc-50 (1.83)
- **Relevant (qrels):** ac-6(2), ac-5(2), ac-6.1(1)
- **Recall:** 0.667 exact-ID / 1.000 base-family · **NDCG@5:** 0.737
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [ac-6, sa-17.7, ac-2.7, ac-3.7, ac-5, sc-50, ac-4.21, sc-7.20, cm-7.6]; invented [none]
- **Answer:** Least privilege is enforced by granting users and processes only the accesses necessary to accomplish assigned tasks, and by structuring systems and monitoring privileged assignments so those minimal accesses are implemented and revoked when no longer appropriate. Specifically:
- Access is limited to only authorized accesses necessary to perform organizational tasks, implementing the principle of least privilege [ac-6].
- System developers are required to structure security-relevant hardware, software, and firmware to facilitate controlling access with least privilege, so the system architecture supports minimal-privilege enforcement [sa-17.7].
- Privileged user accounts are managed under a defined scheme (e.g., role‑ or attribute‑based), with monitoring of privileged role/attribute assignments and changes, and revocation of access when assignments are no longer appropriate—ensuring privileged rights are constrained and removed as needed [ac-2.7].
- Role-based access control is used to enforce access decisions based on organization-defined roles and authorized users who assume those roles, providing a mechanism to assign only the privileges required by each role [ac-3.7].

Separation of duties is maintained by identifying duties and defining access authorizations to prevent conflict or concentration of control:
- Duties of individuals are identified and documented, and system access authorizations are defined to support separation of duties so that conflicting functions are not combined in a single individual’s access set [ac-5].
- Software-enforced separation and policy enforcement mechanisms isolate security domains to prevent unauthorized crossover of privileges between domains [sc-50].
- Logical or physical separation of information flows is used where required to accomplish needed separations, preventing improper mixing of duties or data flows [ac-4.21].
- Dynamic isolation/segregation capability can be used to isolate system components from others when needed to preserve separation of duties or restrict interactions [sc-7.20].
- User-installed software is required to execute in confined environments with limited privileges, reducing risk that such software could subvert separation controls or gain excessive privileges [cm-7.6].

If you need, I can map these controls to specific policy statements, role definitions, and operational steps (provisioning, monitoring, revocation) for your environment.

### Q13: What controls apply to configuration management and baseline configurations?

- **Plan:** baseline configurations and configuration management
- **Retrieved (5):** cm-2 (3.09), cm-2.6 (2.76), cm-3.3 (2.60), cm-2.2 (2.59), cm-9 (2.39)
- **Relevant (qrels):** cm-2(2), cm-6(2), cm-3(1), cm-7(1)
- **Recall:** 0.250 exact-ID / 0.500 base-family · **NDCG@5:** 0.477
- **Faithfulness:** 5/5 · **Answer relevancy:** 5/5
- **Citations:** grounded [cm-2, cm-2.2, cm-3.3, cm-2.6, cm-9]; invented [none]
- **Answer:** The supplied controls that apply to configuration management and baseline configurations are:

- [cm-2] Baseline Configuration — requires developing, documenting, and maintaining a current system baseline under configuration control and reviewing/updating that baseline on a specified frequency, when assigned circumstances occur, and when components are installed or upgraded [cm-2].

- [cm-2.2] Automation Support for Accuracy and Currency — requires maintaining the currency, completeness, accuracy, and availability of the system baseline using assigned automated mechanisms [cm-2.2].

- [cm-3.3] Automated Change Implementation — requires implementing changes to the current system baseline and deploying the updated baseline across the installed base using assigned automated mechanisms [cm-3.3].

- [cm-2.6] Development and Test Environments — requires maintaining a baseline configuration for development and test environments that is managed separately from the operational baseline [cm-2.6].

- [cm-9] Configuration Management Plan — requires developing, documenting, and implementing a configuration management plan that covers roles/responsibilities and processes, a process for identifying and managing configuration items across the lifecycle, definition and placement of configuration items under management, review/approval by assigned personnel/roles, and protection of the plan from unauthorized disclosure/modification [cm-9].

If you need, I can map these controls to specific policy language (e.g., review frequency placeholders, automated mechanism definitions, or roles to assign in [Assignment:] fields).

### Q14: What is the capital of France? (out-of-domain)

- **Expected:** safe fallback — returned the safe fallback ✓
- **Answer:** I could not find any NIST SP 800-53 control relevant to that question in the policy library, so I cannot answer it. Try rephrasing the question in terms of a security control, a control family, or a control ID such as AC-2.

### Q15: How do I bake a chocolate cake? (out-of-domain)

- **Expected:** safe fallback — returned the safe fallback ✓
- **Answer:** I could not find any NIST SP 800-53 control relevant to that question in the policy library, so I cannot answer it. Try rephrasing the question in terms of a security control, a control family, or a control ID such as AC-2.
