from dbx_sync_manager import auth, sync
from dbx_sync_manager.app import DbxApp
from dbx_sync_manager.tree import Tree


def main():
    DbxApp(auth.get_client(), Tree.load(), sync.local_root()).run()


if __name__ == "__main__":
    main()
