'use strict';
// The release the site hands out. The exe lives under a versioned path so /download/Keyware.exe
// can go through the counter (a real file at that path would be served before any rewrite).
const VERSION = '1.3.4';
const FILE_PATH = `/download/${VERSION}/Keyware.exe`;
module.exports = { VERSION, FILE_PATH };
