# Palantir Foundry Docs — /docs/foundry/assist/aip-assist-registering-content

> 来源: https://www.palantir.com/docs/foundry/assist/aip-assist-registering-content/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Register custom content sources with AIP Assist

Use AIP Assist to speed up workflows, onboarding, and automate support by allowing it to deliver targeted instructions from custom content sources. You can allow AIP Assist to serve existing content from in-platform [custom documentation](/docs/foundry/custom-docs/overview/) or from Notepad documents by *registering* your content source to make it available to AIP Assist. To do so, follow the steps below for Notepad or in-platform [custom documentation](/docs/foundry/custom-docs/overview/), depending on your content source. After that, [additional configuration in Control Panel](/docs/foundry/assist/adding-documentation-to-aip-assist/) is necessary to allow AIP Assist to serve your content when answering user queries.

In most instances, we recommend using Notepad due to its versatile use across the Palantir platform through embedding in other applications, like Workshop. To learn more, consult the [Notepad documentation](/docs/foundry/notepad/overview/). If in-platform [custom documentation](/docs/foundry/custom-docs/overview/) is a better option for your use case but is not yet enabled, contact your Palantir representative to enable it for your enrollment.

We highly recommend reviewing the [best practices for custom content sources](/docs/foundry/assist/custom-documentation-best-practices/) as you write and update your content.

## Notepad as a content source for AIP Assist

This feature may not currently be available on all enrollments. If you do not see the option to add Notepad documents to AIP Assist, contact your Palantir representative to find out if your enrollment is eligible.

The following instructions detail how to add a Notepad document as an AIP Assist custom content source:

1. Create a Notepad document by selecting **+ New Document**, or navigate to an existing document. Ensure that your document belongs to a [project](/docs/foundry/compass/create-a-project/).
2. Open the **Actions** dropdown in the top right corner of your Notepad document and select **Add to AIP Assist**. This will open a dialog where you can grant AIP Assist access to your document. It may take some time for this to appear as the document becomes discoverable to AIP Assist.

![The “Add to AIP Assist” option in the Notepad actions dropdown menu.](/docs/resources/foundry/assist/aip-assist-add-notepad-docs.png)

3. Toggle the **Add to AIP Assist** option in the dialog and fill in the **Documentation title** and **Description** fields. These fields should be descriptive and specific to the content in your document.

![The "Add to AIP Assist toggle, Documentation title, and Description fields.](/docs/resources/foundry/assist/aip-assist-description.png)

4. Select **Save** and ensure that a success message is displayed, confirming that the document has been ingested into AIP Assist.

![A sample success message after ingesting a Notepad document.](/docs/resources/foundry/assist/aip-assist-ingestion-success.png)

Your content has now been ingested into AIP Assist and will be available in Control Panel for [visibility configuration](/docs/foundry/assist/adding-documentation-to-aip-assist/#enable-content-source-visibility-for-your-users). Ensure that you first configure visibility settings for Notepad documents by user and group, as existing permissions are respected by AIP Assist.

## In-platform custom documentation as a content source for AIP Assist

To use this feature, you must already have a documentation repository in Code Repositories. Contact your Palantir representative to enable `documentation` type repositories if this feature is not available for your enrollment, or to whitelist the repository after creation if it is already enabled.

![Initialize a Documentation repository from the Documentation template.](/docs/resources/foundry/assist/documentation-template.png?width=800)

To grant AIP Assist access to your in-platform [custom documentation](/docs/foundry/custom-docs/overview/), add content to your documentation repository and opt-in to provide the information to AIP Assist.

1. Make sure you have initialized a documentation repository in Code Repositories. Populate it with content by creating a new folder, or “product” in the **docs** folder.

![Create a product folder.](/docs/resources/foundry/assist/product-folder.png?width=700)

2. In a “product” folder (“Custom\_Docs\_Support\_Alert\_Example” below), create an `overview.md` file with `@name` and `@description`, followed by your documentation in Markdown format.

![Create your @name, @description followed by documentation in your new product folder.](/docs/resources/foundry/assist/overview-md.png)

3. When you have finished populating your documentation repository with content, rename the existing `_aip-assist.json` file, located in the top-level of the repo, to `aip-assist.json`. *The original file may be regenerated after some time; no action is required.*
4. In this file, list the "product" folder you want to opt in to AIP Assist along with a description. The description will be provided to AIP Assist so it can assess when to query custom documentation. Ensure that the description is comprehensive and compact. Commit your changes and ensure that the documentation is published as part of the checks.

![Opt-in to ingesting it to AIP Assist.](/docs/resources/foundry/assist/opt-in.png)

## FAQ

### Will content updates in custom documentation or Notepad documents be reflected in the information AIP Assist provides to users?

Every update in the in-platform custom documentation or Notepad documents will automatically propagate and update our databases. No further action is necessary to update content in AIP Assist.

### Why are images not visible in AIP Assist responses?

For now, AIP Assist only displays images from Palantir's public documentation and in-platform custom documentation, but we are working on support for Notepad images as well. In the meantime, we recommend adding descriptions below images. This text will be surfaced in AIP Assist, and in-line citations will be added to answers. When users select a citation, they will be redirected to your content where they can view any relevant images.

### Why is the AIP Assist button not visible under actions in Notepad?

Currently, you cannot add empty Notepad documents to AIP Assist, so make sure your document includes content. If you have just added content or pasted a large document into Notepad, allow it some time to update, as new resources can take some time to propagate.

Make sure that you have AIP enabled on your enrollment, that your Notepad document is in a [project](/docs/foundry/compass/create-a-project/), and that the project has organization [Markings](/docs/foundry/security/markings/).
