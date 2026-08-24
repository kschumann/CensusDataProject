## The Census Data Project
I have long had "playing around with census data" on my to do list, but was never able to make the time for it until coding assistants came around.  I used my AI assistant to help me plumb the depths of census.gov annual population estimates + survey data and then extract variables I thought were interesting using Python scripts.  It then coded my vision of an interactive map using d3.js.

The main idea is to get a birds eye view of the US at a granular (county) level and be able to "play" through changes in the data over the years.  I added in the ability to drill into a single county and see change over time at a glance, with both actual and normalized data views.  Plans for the future include adding some more variables and cleaning up gaps in the data.

I have the final product hosted on cloudflare's free tier: <a href="https://censusdataproject.org" target="_blank">censusdataproject.org</a>
