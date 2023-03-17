import frappe
import os
import io
import zipfile
from urllib import request

from frappe.utils import (cint, encode, get_files_path, get_url)
from frappe.core.doctype.file.file import (File, get_content_hash)
from six import PY2
from six.moves.urllib.parse import (quote, unquote)


class MyFile(File):
    def generate_content_hash(self):
        if self.content_hash or not self.file_url or self.file_url.startswith("http"):
            return
        try:
            content = self.get_content()
            self.content_hash = get_content_hash(content)
        except IOError:
            frappe.throw(_("File {0} does not exist").format(self.file_url))

    def validate_url(self):
        if self.file_url.startswith("/api/method/frappe_s3_attachment.controller.generate_file"):
            return

        if not self.file_url or self.file_url.startswith(("http://", "https://")):
            if not self.flags.ignore_file_validate:
                self.validate_file()

            return

        # Probably an invalid web URL
        if not self.file_url.startswith(("/files/", "/private/files/")):
            frappe.throw(_("URL must start with http:// or https://"),
                         title=_("Invalid URL"))

        # Ensure correct formatting and type
        self.file_url = unquote(self.file_url)
        self.is_private = cint(self.is_private)

        self.handle_is_private_changed()

        base_path = os.path.realpath(get_files_path(is_private=self.is_private))
        if not os.path.realpath(self.get_full_path()).startswith(base_path):
            frappe.throw(_("The File URL you've entered is incorrect"),
                         title=_("Invalid File URL"))

    def get_full_path(self):
        """Returns file path from given file name"""

        file_path = self.file_url or self.file_name

        if "/" not in file_path:
            file_path = "/files/" + file_path

        if file_path.startswith("/private/files/"):
            file_path = get_files_path(
                *file_path.split("/private/files/", 1)[1].split("/"), is_private=1)

        elif file_path.startswith("/files/"):
            file_path = get_files_path(*file_path.split("/files/", 1)[1].split("/"))

        elif file_path.startswith("/api/"):
            deli_str = '&file_name='
            idx_file_name = file_path.index(deli_str) + len(deli_str)
            file_name = file_path[idx_file_name:]
            quote_file_path = file_path[0:idx_file_name] + quote(file_name)
            file_path = get_url(quote_file_path)

        elif file_path.startswith("http"):
            pass

        elif not self.file_url:
            frappe.throw(
                _("There is some problem with the file url: {0}").format(file_path))

        return file_path

    def get_content(self, sid=frappe.session.sid):
        """Returns [`file_name`, `content`] for given file name `fname`"""
        if self.is_folder:
            frappe.throw(_("Cannot get file contents of a Folder"))

        if self.get("content"):
            return self.content

        self.validate_url()
        file_path = self.get_full_path()

        # read the file
        if PY2:
            with open(encode(file_path)) as f:
                content = f.read()
        elif file_path.startswith("http"):
            opener = request.build_opener()
            opener.addheaders = [
                ('User-Agent', 'Mozilla/5.0'),
                ('Cookie', f'sid={sid}')
            ]
            with opener.open(file_path) as f:
                content = f.read()
        else:
            with io.open(encode(file_path), mode="rb") as f:
                content = f.read()
                try:
                    # for plain text files
                    content = content.decode()
                except UnicodeDecodeError:
                    # for .png, .jpg, etc
                    pass

        self.content = content
        return self.content

    def unzip(self):
        """Unzip current file and replace it by its children"""
        if not self.file_url.endswith(".zip"):
            frappe.throw(_("{0} is not a zip file").format(self.file_name))

        file_path = self.get_full_path()

        if file_path.startswith("http"):
            opener = request.build_opener()
            opener.addheaders.append(('Cookie', f'sid={frappe.session.sid}'))
            with opener.open(file_path) as f:
                content = f.read()
                zip_path = io.BytesIO(content)
        else:
            zip_path = file_path

        files = []
        with zipfile.ZipFile(zip_path) as z:
            for file in z.filelist:
                if file.is_dir() or file.filename.startswith("__MACOSX/"):
                    # skip directories and macos hidden directory
                    continue

                filename = os.path.basename(
                    file.filename.encode('cp437').decode('utf-8'))
                if filename.startswith("."):
                    # skip hidden files
                    continue

                file_doc = frappe.new_doc("File")
                file_doc.content = z.read(file.filename)
                file_doc.file_name = filename
                file_doc.folder = self.folder
                file_doc.is_private = self.is_private
                file_doc.attached_to_doctype = self.attached_to_doctype
                file_doc.attached_to_name = self.attached_to_name
                file_doc.save()
                files.append(file_doc)

        frappe.delete_doc("File", self.name)
        return files
