'use strict';
// The release the site hands out. The exe lives under a versioned path so /download/Keyware.exe
// can go through the counter (a real file at that path would be served before any rewrite).
const VERSION = '1.3.8';
const FILE_PATH = `/download/${VERSION}/Keyware.exe`;
// SHA-256 of the published Keyware.exe for VERSION (GitHub release v1.3.8).
const SHA256 = '9b792c19fa2d2684dfd2d64bc252a1f0df5114084667e1a5ac930db48063bf09';
module.exports = { VERSION, FILE_PATH, SHA256 };
