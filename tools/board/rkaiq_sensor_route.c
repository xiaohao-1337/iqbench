/* Resolve the active sensor upstream of a Rockchip mainpath device. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/media.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

struct sensor_route {
    char name[32];
    int ambiguous;
};

static int next_entity(int fd, struct media_entity_desc *entity)
{
    entity->id |= MEDIA_ENT_ID_FLAG_NEXT;
    return ioctl(fd, MEDIA_IOC_ENUM_ENTITIES, entity) == 0;
}

static struct media_link_desc *entity_links(int fd, const struct media_entity_desc *entity)
{
    if (!entity->links)
        return NULL;
    struct media_link_desc *links = calloc(entity->links, sizeof(*links));
    if (!links)
        return NULL;
    struct media_links_enum request = {.entity = entity->id, .links = links};
    if (ioctl(fd, MEDIA_IOC_ENUM_LINKS, &request) != 0) {
        free(links);
        return NULL;
    }
    return links;
}

static int data_link(const struct media_link_desc *link, unsigned source)
{
    return link->source.entity == source && (link->flags & MEDIA_LNK_FL_ENABLED) &&
           (link->flags & MEDIA_LNK_FL_LINK_TYPE) == MEDIA_LNK_FL_DATA_LINK;
}

static void add_sensor(struct sensor_route *route, const char *name)
{
    if (route->name[0])
        route->ambiguous |= strcmp(route->name, name) != 0;
    else
        snprintf(route->name, sizeof(route->name), "%s", name);
}

static int same_model(const char *a, const char *b)
{
    while (*a && *b) {
        char first = *a++, second = *b++;
        if ((first == '_' ? '-' : first) != (second == '_' ? '-' : second))
            return 0;
    }
    return *a == *b;
}

static void find_cif_sensors(const glob_t *media, const char *cif, struct sensor_route *route)
{
    for (size_t index = 0; index < media->gl_pathc && !route->ambiguous; ++index) {
        int fd = open(media->gl_pathv[index], O_RDONLY | O_CLOEXEC);
        if (fd < 0)
            continue;
        struct media_device_info info = {0};
        if (ioctl(fd, MEDIA_IOC_DEVICE_INFO, &info) == 0 &&
            strncmp(info.driver, "rkcif", 5) == 0 && same_model(info.model, cif)) {
            struct media_entity_desc sensor = {0};
            while (!route->ambiguous && next_entity(fd, &sensor)) {
                if (sensor.type != MEDIA_ENT_F_CAM_SENSOR)
                    continue;
                struct media_link_desc *links = entity_links(fd, &sensor);
                for (unsigned i = 0; links && i < sensor.links; ++i) {
                    if (data_link(&links[i], sensor.id)) {
                        add_sensor(route, sensor.name);
                        break;
                    }
                }
                free(links);
            }
        }
        close(fd);
    }
}

static void follow_input(int fd, const glob_t *media, unsigned sink, int pad, unsigned depth,
                         struct sensor_route *route)
{
    if (!depth)
        return;
    struct media_entity_desc source = {0};
    while (!route->ambiguous && next_entity(fd, &source)) {
        struct media_link_desc *links = entity_links(fd, &source);
        for (unsigned i = 0; links && i < source.links; ++i) {
            const struct media_link_desc *link = &links[i];
            if (!data_link(link, source.id) || link->sink.entity != sink ||
                (pad >= 0 && link->sink.index != (unsigned)pad))
                continue;
            if (source.type == MEDIA_ENT_F_CAM_SENSOR)
                add_sensor(route, source.name);
            else if (strncmp(source.name, "rkcif", 5) == 0)
                find_cif_sensors(media, source.name, route);
            else
                follow_input(fd, media, source.id, -1, depth - 1, route);
        }
        free(links);
    }
}

static int mainpath_input(int fd, dev_t video, unsigned *isp)
{
    struct media_device_info info = {0};
    if (ioctl(fd, MEDIA_IOC_DEVICE_INFO, &info) != 0 || strncmp(info.driver, "rkisp", 5) != 0)
        return 0;
    struct media_entity_desc entity = {0};
    int mainpath = 0;
    while (next_entity(fd, &entity)) {
        if (strcmp(entity.name, "rkisp_mainpath") == 0 &&
            entity.dev.major == major(video) && entity.dev.minor == minor(video))
            mainpath = 1;
        if (strcmp(entity.name, "rkisp-isp-subdev") == 0)
            *isp = entity.id;
    }
    return mainpath && *isp;
}

const char *rk_aiq_uapi2_sysctl_getBindedSnsEntNmByVd(const char *video)
{
    struct stat node;
    glob_t media = {0};
    struct sensor_route route = {0};
    int found = 0;
    if (video && stat(video, &node) == 0 && S_ISCHR(node.st_mode) &&
        glob("/dev/media[0-9]*", GLOB_NOSORT, NULL, &media) == 0) {
        for (size_t index = 0; index < media.gl_pathc; ++index) {
            int fd = open(media.gl_pathv[index], O_RDONLY | O_CLOEXEC);
            if (fd < 0)
                continue;
            unsigned isp = 0;
            found = mainpath_input(fd, node.st_rdev, &isp);
            if (found)
                follow_input(fd, &media, isp, 0, 16, &route);
            close(fd);
            if (found)
                break;
        }
    }
    globfree(&media);
    if (!found) {
        const char *(*original)(const char *) = dlsym(RTLD_NEXT, "rk_aiq_uapi2_sysctl_getBindedSnsEntNmByVd");
        return original && video ? original(video) : NULL;
    }
    if (route.ambiguous || !route.name[0]) {
        fprintf(stderr, "RKAIQ route: could not resolve one active sensor for %s\n", video);
        return NULL;
    }
    static _Thread_local char active_sensor[32];
    snprintf(active_sensor, sizeof(active_sensor), "%s", route.name);
    fprintf(stderr, "RKAIQ route: %s -> %s\n", video, active_sensor);
    return active_sensor;
}
