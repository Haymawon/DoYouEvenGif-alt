const DOWNLOADS = {
    "ladies-with-guns": {
        title: "Ladies with Guns",
        description: "The English edition has 3 parts. Choose a part below.",
        files: [
            {
                name: "Ladies with Guns - Part 1",
                file: "https://github.com/Haymawon/DoYouEvenGif-alt/releases/download/comic/Ladies-with-Guns-part-1.cbr",
                meta: "Part 1"
            },
            {
                name: "Ladies with Guns - Part 2",
                file: "https://github.com/Haymawon/DoYouEvenGif-alt/releases/download/comic/Ladies-with-Guns-Part-2.cbr",
                meta: "Part 2"
            },
            {
                name: "Ladies with Guns - Part 3",
                file: "https://github.com/Haymawon/DoYouEvenGif-alt/releases/download/comic/Ladies-with-Guns-Part-3.cbr",
                meta: "Part 3"
            }
        ]
    },

    "lola-xoxo": {
        title: "Lola XOXO",
        description: "Choose a file below to start your download.",
        files: [
            {
                name: "Lola XOXO V1",
                file: "Lola-XOXO-v1(2014-2015) .zip",
                meta: "Download"
            },
            {
                name: "Lola XOXO Wasteland Madam",
                file: "Lola-XOXO-Wasteland-Madam(2015-2016).zip",
                meta: "Download"
            },
            {
                name: "Lola XOXO V2",
                file: "Lola-XOXO-v2(2017).zip",
                meta: "Download"
            },
            {
                name: "Lola XOXO V3",
                file: "Lola-XOXO-v3(2019-2020).zip",
                meta: "Download"
            }
        ]
    },
    "azimuth": {
        title: "Azimuth Ongoing • 1 volume",
        description: "Choose a file below to start your download.",
        files: [
            {
                name: "Azimuth V1",
                file: "https://github.com/Haymawon/DoYouEvenGif-alt/releases/download/comic/Azimuth-Vol-1(2026).cbz",
                meta: "Download"
            }
        ]
    },
    "fairest": {
        title: "Fairest Completed • 5 volumes",
        description: "Choose a file below to start your download.",
        files: [
            {
                name: "Fairest V1",
                file: "Fairest-v01-Wide-Awake(2012).cbr",
                meta: "Download"
            },
            {
                name: "Fairest V2",
                file: "Fairest-v02-The-Hidden-Kingdom(2013).cbr",
                meta: "Download"
            },
            {
                name: "Fairest V3",
                file: "Fairest-v03-The-Return-of-the-Maharaja-(2014).cbr",
                meta: "Download"
            },
            {
                name: "Fairest V4",
                file: "Fairest-v04-Of-Men-and-Mice(2014).cbr",
                meta: "Download"
            },
            {
                name: "Fairest V5",
                file: "Fairest-v05-The-Clamour-for-Glamour-(2015).cbr",
                meta: "Download"
            }
        ]
    }
};

const params = new URLSearchParams(window.location.search);
const post = params.get("post");

const title = document.getElementById("downloadTitle");
const description = document.getElementById("downloadDescription");
const list = document.getElementById("downloadList");
const notFound = document.getElementById("notFound");

function createFileItem(item) {
    const row = document.createElement("div");
    row.className = "file-item";

    const info = document.createElement("div");
    info.className = "file-info";

    const name = document.createElement("h2");
    name.className = "file-name";
    name.textContent = item.name;

    const meta = document.createElement("p");
    meta.className = "file-meta";
    meta.textContent = item.meta;

    info.append(name, meta);

    const link = document.createElement("a");
    link.className = "download-button";
    link.href = item.file;
    link.textContent = "Download";
    link.target = "_blank";
    link.rel = "noopener";

    row.append(info, link);

    return row;
}

const data = DOWNLOADS[post];

if (!data) {
    title.textContent = "Download";
    description.hidden = true;
    notFound.hidden = false;
} else {
    document.title = `${data.title} - Download`;
    title.textContent = data.title;
    description.textContent = data.description;

    data.files.forEach((file) => {
        list.appendChild(createFileItem(file));
    });
}
