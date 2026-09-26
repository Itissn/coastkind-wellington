# Coastkind presentation

This is an English, read-only demonstration of a coastal community around Wellington. All observations, illustrations, accounts, analysis, review decisions, points and attention signals are fictional. They are provided for presentation and analysis practice, not environmental findings or real benefits. Do not submit or sell them as evidence.

Open `index.html` through a web server to explore the coastal map and community stories. Open `demo.html` to filter the dataset and inspect each record. The `downloads` directory contains synthetic CSV and JSON exports plus a data dictionary. Modern browsers block some file-based requests, so double-clicking an HTML file is not sufficient.

## Publish the complete project on GitHub Pages

The complete project keeps its Python backend and editable source at the repository root, and the generated static presentation in `docs/`.

1. Build the presentation with `python build_presentation.py --out docs` and commit the generated `docs/` directory with the project source. Keep local databases, `.env`, private data and voucher stock out of the repository.
2. Open the repository's **Settings → Pages**. Under **Build and deployment**, choose **Deploy from a branch**, select `main` and the **/docs** folder, then save.
3. Open the site URL shown by GitHub after deployment. Its map, dataset explorer and simulated analysis examples work below the repository path.

Choosing **/(root)** for the complete source project would serve the development page, which expects a Python backend. Choose **/docs** for the prepared static presentation. Source code can be viewed in the repository; the Python backend does not run on GitHub Pages.

## Publish the presentation package on its own

1. Create a GitHub repository for the presentation.
2. Upload the **contents** of this package to the repository root, including `index.html`, `demo.html`, `data`, `images`, `downloads`, the JavaScript and CSS files, and `.nojekyll`. Do not upload the local project's SQLite database, `.env`, or backend files.
3. Open the repository's **Settings → Pages**. Under **Build and deployment**, choose **Deploy from a branch**, select the branch containing these files (usually `main`) and the **/(root)** folder, then save.
4. Wait for GitHub's deployment and open the site URL shown on that page. The presentation works below a repository path, such as `https://YOUR-NAME.github.io/YOUR-REPOSITORY/`.

See [GitHub's publishing source instructions](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site) for the current repository settings. Building the package does not publish it; deployment is a separate step.

## What works

- Wellington map and community selection; internet access is needed for the map library and OpenStreetMap tiles.
- Fictional observations, community concerns, simulated analysis and attention signals.
- Dataset filtering, record inspection and synthetic dataset downloads.

Uploads, registration, sign-in, AI requests, actual rewards and database writes are disabled. GitHub Pages hosts static files and does not run the Python backend. All files uploaded here are publicly downloadable, which is why this package contains only marked synthetic data and no credentials, database or voucher codes.

## Rebuild from the local project

From `C:\SDG14`, run:

```powershell
& 'C:\LSVRP Program\Anaconda\python.exe' seed_demo.py
& 'C:\LSVRP Program\Anaconda\python.exe' build_presentation.py
```

The builder produces `presentation/` and `presentation.zip` from an explicit list of static assets and the marked demonstration database. For the complete project's Pages site, add `--out docs` to generate `docs/` and `docs.zip` instead. It refuses unrelated nonempty output folders. To preview the standalone generated package locally, run:

```powershell
& 'C:\LSVRP Program\Anaconda\python.exe' -m http.server 8002 --bind 127.0.0.1 --directory presentation
```

Then visit `http://localhost:8002/`. Stop this temporary server with Ctrl+C.
