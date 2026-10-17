// createIntegrationUser.apex — idempotently ensure a dedicated CI/CD integration
// user exists and carries the SF_CICD_Deploy permission set.
//
// Placeholders (replaced by create-integration-user.sh before execution):
//   __USERNAME__   full username (must be globally unique, e.g. cicd@acme.com.int)
//   __EMAIL__      contact email
//   __ALIAS__      <= 8 char alias
//   __LASTNAME__   surname shown in Setup
//   __PROFILE__    profile name to assign (e.g. System Administrator)
//   __PERMSET__    API name of the permission set (SF_CICD_Deploy)
//   __APPLABEL__   Connected App label to pre-authorize for the permission set
//                  (ConnectedApplication.Name, e.g. "SF CICD Deploy")
//
// Notes:
//   * Salesforce Users are records, not metadata, so they cannot be deployed —
//     this anonymous Apex is the low-touch way to create one in each org.
//   * Re-runnable: if the user already exists it is left as-is and only the
//     permission-set assignment is ensured.
//   * IMPORTANT (CI/CD): the integration user MUST land on a profile that has NO
//     Login IP Ranges, otherwise JWT auth from GitHub-hosted runners is blocked
//     with a misleading "audience is invalid" (really "ip restricted"). The
//     default System Administrator profile is normally IP-range-free.
//   * The Connected App uses "Admin approved users are pre-authorized", so the
//     permission set is linked to the app via a SetupEntityAccess record below.
//     Without this, JWT fails with "user is not admin approved to access this app".

String username   = '__USERNAME__';
String email      = '__EMAIL__';
String userAlias  = '__ALIAS__';
String lastName   = '__LASTNAME__';
String profileNm  = '__PROFILE__';
String permsetNm  = '__PERMSET__';
String appLabel   = '__APPLABEL__';

Profile p = [SELECT Id FROM Profile WHERE Name = :profileNm LIMIT 1];

List<User> existing = [SELECT Id FROM User WHERE Username = :username LIMIT 1];
User u;
if (existing.isEmpty()) {
    u = new User(
        Username          = username,
        Email             = email,
        LastName          = lastName,
        Alias             = userAlias,
        ProfileId         = p.Id,
        TimeZoneSidKey    = 'America/Los_Angeles',
        LocaleSidKey      = 'en_US',
        EmailEncodingKey  = 'UTF-8',
        LanguageLocaleKey = 'en_US'
    );
    insert u;
    System.debug('INTEGRATION_USER_STATUS=CREATED');
} else {
    u = existing[0];
    System.debug('INTEGRATION_USER_STATUS=EXISTS');
}

List<PermissionSet> pss = [SELECT Id FROM PermissionSet WHERE Name = :permsetNm LIMIT 1];
if (!pss.isEmpty()) {
    Integer already = [
        SELECT COUNT() FROM PermissionSetAssignment
        WHERE AssigneeId = :u.Id AND PermissionSetId = :pss[0].Id
    ];
    if (already == 0) {
        insert new PermissionSetAssignment(AssigneeId = u.Id, PermissionSetId = pss[0].Id);
        System.debug('PERMSET_STATUS=ASSIGNED');
    } else {
        System.debug('PERMSET_STATUS=ALREADY_ASSIGNED');
    }
} else {
    System.debug('PERMSET_STATUS=NOT_FOUND(' + permsetNm + ')');
}

// Pre-authorize the permission set for the Connected App (SetupEntityAccess).
// The app is "Admin approved users are pre-authorized"; linking the permset here
// makes every user carrying it (incl. this CI user) approved for JWT.
if (!pss.isEmpty() && String.isNotBlank(appLabel) && !appLabel.startsWith('__')) {
    List<ConnectedApplication> apps =
        [SELECT Id FROM ConnectedApplication WHERE Name = :appLabel LIMIT 1];
    if (apps.isEmpty()) {
        System.debug('APP_PREAUTH_STATUS=APP_NOT_FOUND(' + appLabel + ')');
    } else {
        Integer linked = [
            SELECT COUNT() FROM SetupEntityAccess
            WHERE ParentId = :pss[0].Id AND SetupEntityId = :apps[0].Id
        ];
        if (linked == 0) {
            // SetupEntityType is auto-derived from SetupEntityId on insert.
            insert new SetupEntityAccess(ParentId = pss[0].Id, SetupEntityId = apps[0].Id);
            System.debug('APP_PREAUTH_STATUS=AUTHORIZED');
        } else {
            System.debug('APP_PREAUTH_STATUS=ALREADY_AUTHORIZED');
        }
    }
}

System.debug('INTEGRATION_USER_ID=' + u.Id);
