# Palantir Foundry Docs — /docs/foundry/developer-console/oauth-clients

> 来源: https://www.palantir.com/docs/foundry/developer-console/oauth-clients
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Standalone OAuth clients [Legacy]

Legacy

Standalone OAuth clients are now legacy and can no longer be created. To set up a new client, follow the steps to [create an unrestricted application](/docs/foundry/developer-console/application-restrictions/#create-an-unrestricted-application). To move an existing client, see [Migrate standalone OAuth clients](#migrate-standalone-oauth-clients) below.

Standalone OAuth clients are a legacy, lightweight alternative to custom applications. Consider replacing a standalone OAuth client with an [unrestricted custom application](/docs/foundry/developer-console/application-restrictions/#unrestricted-applications), which provides new features like restrictions, metrics, and SDK generation.

Users can no longer create standalone OAuth clients. Existing standalone OAuth clients can be migrated to Developer Console applications.

## Migrate standalone OAuth clients

Migrating your standalone OAuth client to a Developer Console application provides access to the following features:

- Ontology and Platform SDKs
- Website hosting
- Application restrictions
- DevOps support
- Metrics

To migrate your standalone OAuth client:

1. Open your existing standalone OAuth client.
2. Select **Migrate to Developer Console application**.
3. Select a location and maximum classification, if applicable, in the migration wizard.
4. Review user permissions for the Developer Console application.
5. Select **Submit** to complete the migration.

After migration, the newly created application appears in the selected location. You can perform actions on the application like those on any other Developer Console application. Existing workflows using your client remain unaffected.

## Manage a standalone OAuth client in Developer Console

[Navigate to the OAuth clients list](/docs/foundry/developer-console/overview/#developer-console-landing-page) and select the client you want to manage. To manage an OAuth client, you must have the **Manage OAuth 2.0 clients** permission in the OAuth client's owning organization.

You will now see the **Overview** page, where you can edit application details such as application name, description, and logo. You may also choose to delete the application if you expand the **Danger zone**.

![The standalone OAuth client Overview page displays application details and settings.](/docs/resources/foundry/developer-console/oauth-client-overview.png)

From here, you can navigate to the **OAuth & Permissions** page, where you can view and manage client details. You may also choose to share the application with other organizations, which will allow them to enable the application for their own users.

![The OAuth and permissions page shows client configuration options.](/docs/resources/foundry/developer-console/oauth-client-oauth.png)
