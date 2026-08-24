const fs = require('fs');
const path = require('node:path');

exports.default = async function(context) {
    console.log("After-pack cleanup hook (keeping PCA.tar.gz for packaging step)...");
    // Don't delete PCA.tar.gz here - the after-build-hook needs it
    // It will be cleaned up by the packaging hook after it copies it
};