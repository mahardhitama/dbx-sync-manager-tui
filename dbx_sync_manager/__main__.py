from dbx_sync_manager import auth, sync
from dbx_sync_manager.app import DbxApp
from dbx_sync_manager.tree import Tree


def main():
    creds = auth.load_creds() or auth.login()
    # the token is first checked by the app's background refresh, so a rejected one exits the TUI to log in
    while DbxApp(auth.make_client(creds), Tree.load(), sync.local_root()).run() == "relogin":
        creds = auth.login()


if __name__ == "__main__":
    main()
