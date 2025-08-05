import requests
import xml.etree.ElementTree as ET

class RedditRSSFetcher:
    def __init__(self, subreddit="python"):
        self.subreddit = subreddit
        self.feed_url = f"https://old.reddit.com/r/{self.subreddit}.rss"
        self.headers = {"User-Agent": "MeshbotWeather/1.0"}

    def get_post_titles(self, max_posts=10):
        try:
            response = requests.get(self.feed_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            titles = []
            # Handle namespaces
            ns = {'atom': 'http://www.w3.org/2005/Atom', 'rss': 'http://purl.org/rss/1.0/'}
            # Try RSS 2.0 <item> first
            items = root.findall('.//item')
            # Try Atom <entry> if no <item>
            if not items:
                items = root.findall('.//atom:entry', ns)
            for entry in items:
                # Try both direct and namespaced title
                title_elem = entry.find('title')
                if title_elem is None:
                    title_elem = entry.find('atom:title', ns)
                if title_elem is not None and title_elem.text:
                    titles.append(title_elem.text.strip())
                    print(f"Fetched title: {title_elem.text.strip()}")
                if len(titles) >= max_posts:
                    break
            if not titles:
                print("No titles found. Check RSS format or parsing path.")
            return titles
        except Exception as e:
            print(f"Error fetching or parsing RSS: {e}")
            return [f"Error fetching RSS feed: {e}"]

if __name__ == "__main__":
    fetcher = RedditRSSFetcher("python")
    for i, title in enumerate(fetcher.get_post_titles(), 1):
        print(f"{i}. {title}")
