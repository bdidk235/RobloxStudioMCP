/** Run a snippet of Luau inside Studio and print the result. */

import { RobloxStudio } from "../src/index.js";

async function main(): Promise<void> {
  const studio = await RobloxStudio.connect();
  try {
    // Which Studio instance are we targeting?
    console.log(`studio_id: ${await studio.resolveStudioId()}`);

    const result = await studio.executeLuau(
      "return { name = game.PlaceId, version = version() }",
    );
    console.log("Result:", result.text());
  } finally {
    await studio.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
